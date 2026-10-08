"""Decision routes: generate the shortlist, personal picks, votes, delegation, selection,
manual plans, feedback and data-error reports."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Response

from dining.api.common import (
    new_id,
    now,
    stamp,
)
from dining.api.context import ApiContext
from dining.api.routes.learning import propose_repeat_pattern, remove_observation
from dining.api.schemas import (
    Cancel,
    DataErrorReport,
    Delegation,
    Feedback,
    Generate,
    ManualAcknowledgement,
    ManualPlan,
    PersonalAction,
    Select,
    Vote,
)
from dining.core.constants import DEFAULT_MEAL_MINUTES, POST_DECISION_STATUSES
from dining.core.store import decode, encode
from dining.meals.exposure import (
    EVENT_CANDIDATE_SELECTED,
    EVENT_CANDIDATE_VOTED_ON,
    record_exposure_event,
    record_generation_exposures,
)
from dining.meals.generation import (
    evidence_identity,
    policy_identity,
)
from dining.meals.learning import (
    extract_attribute_signals,
    propose_attribute_patterns,
)
from dining.recommendation.personal import (
    save_personal_recommendations,
    score_personal_options,
)


def register(router: APIRouter, ctx: ApiContext) -> None:
    """Add this module's routes to ``router``."""
    # Shared helpers, bound to local names so the route bodies read naturally.
    Auth = ctx.Auth
    approved_users = ctx.approved_users
    async_generation = ctx.async_generation
    audit = ctx.audit
    generation_worker = ctx.generation_worker
    invalidate = ctx.invalidate
    meal_member = ctx.meal_member
    meal_view = ctx.meal_view
    notify = ctx.notify
    option_check = ctx.option_check
    recommend = ctx.recommend
    revision_check = ctx.revision_check
    safe_recommend = ctx.generation.safe_recommend
    snapshot_for = ctx.generation.snapshot_for
    store = ctx.store

    @router.post("/meals/{meal_id}/generate")
    def generate(meal_id: str, body: Generate, response: Response, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            revision_check(meal, body.expected_revision)
            if meal["status"] == "manual_proposed":
                raise HTTPException(
                    409,
                    "Revise the manual plan or update the meal context before requesting a checked shortlist",
                )
            if meal["status"] == "generating" and not async_generation:
                raise HTTPException(409, "This version is already being checked")
            if datetime.fromisoformat(decode(meal["payload"])["decision_by"]) <= now():
                raise HTTPException(
                    409, "The decision deadline passed. Update meal times first"
                )
            rows = db.execute(
                "SELECT * FROM participants WHERE meal_id=?", (meal_id,)
            ).fetchall()
            all_ids = {r["user_id"] for r in rows}
            joined = {r["user_id"] for r in rows if r["attendance"] == "join"}
            included = (
                set(body.participant_ids)
                if body.participant_ids is not None
                else joined
            )
            pending = {r["user_id"] for r in rows if r["attendance"] == "pending"}
            if pending and not body.exclude_pending:
                raise HTTPException(
                    409,
                    "Some invitees have not answered. Explicitly confirm their exclusion or wait",
                )
            if (
                not 2 <= len(included) <= 8
                or not included.issubset(joined)
                or auth["user_id"] not in included
            ):
                raise HTTPException(
                    409,
                    "Include 2–8 joined, ready participants including the organizer",
                )
            # Changing the explicitly included set produces a fresh revision, even if answers stayed identical.
            previous = decode(meal["frozen_participants"])
            if previous is not None and set(previous) != included:
                invalidate(db, meal_id)
                meal = db.execute(
                    "SELECT * FROM meals WHERE id=?", (meal_id,)
                ).fetchone()
            if async_generation:
                previous_job = db.execute(
                    "SELECT * FROM generation_jobs WHERE meal_id=? AND context_revision=? ORDER BY rowid DESC LIMIT 1",
                    (meal_id, meal["revision"]),
                ).fetchone()
                if previous_job is not None and (
                    previous_job["status"] == "superseded"
                    or previous_job["evidence_revision"] != evidence_identity(recommend)
                    or previous_job["policy_version"] != policy_identity()
                ):
                    # Shared-version CAS also binds votes/delegation to this
                    # recommendation set, even if provider option IDs are reused.
                    invalidate(db, meal_id)
                    meal = db.execute(
                        "SELECT * FROM meals WHERE id=?", (meal_id,)
                    ).fetchone()
            snapshot = snapshot_for(db, meal, sorted(included))
            revision = meal["revision"]
            if async_generation:
                _job, created = generation_worker.enqueue(
                    db, meal, snapshot, evidence_identity(recommend)
                )
                response.status_code = 202
                if not created:
                    return meal_view(db, meal, auth["user_id"])
            db.execute(
                "UPDATE meals SET status='generating',result=NULL,result_revision=NULL,frozen_participants=? WHERE id=?",
                (encode(sorted(included)), meal_id),
            )
            db.execute("DELETE FROM votes WHERE meal_id=?", (meal_id,))
            db.execute("DELETE FROM delegations WHERE meal_id=?", (meal_id,))
            notify(
                db,
                all_ids - included,
                "meal_exclusion",
                "The organizer is checking options for a smaller participant set. You are not included in that decision.",
                meal["room_id"],
                meal_id,
            )
            audit(db, auth["user_id"], "generation_started", meal["room_id"], meal_id)
            if async_generation:
                return meal_view(
                    db,
                    db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                    auth["user_id"],
                )
        try:
            result = safe_recommend(snapshot)
        except Exception:  # noqa: BLE001 — the provider boundary must not disclose private exception text
            with store.transaction() as db:
                db.execute(
                    "UPDATE meals SET status='collecting' WHERE id=? AND revision=? AND status='generating'",
                    (meal_id, revision),
                )
            raise HTTPException(
                503,
                "Recommendations are temporarily unavailable. Your answers are saved",
            ) from None
        with store.transaction() as db:
            current = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            if current["revision"] != revision or current["status"] != "generating":
                raise HTTPException(
                    409, "Answers or membership changed while checking. Generate again"
                )
            db.execute(
                "UPDATE meals SET status=?,result=?,result_revision=? WHERE id=?",
                (result["status"], encode(result), revision, meal_id),
            )
            db.execute(
                "INSERT OR REPLACE INTO recommendation_archives(id,meal_id,revision,payload,created_at) VALUES(?,?,?,?,?)",
                (f"{meal_id}:{revision}", meal_id, revision, encode(result), stamp()),
            )
            record_generation_exposures(db, meal_id, revision, result)
            for uid in included:
                p_row = db.execute(
                    "SELECT p.*, u.profile FROM participants p JOIN users u ON u.id=p.user_id WHERE p.meal_id=? AND p.user_id=?",
                    (meal_id, uid),
                ).fetchone()
                if p_row:
                    diner_dict = {
                        "user_id": uid,
                        "profile": decode(p_row["profile"], {}),
                        "response": decode(p_row["response"], {}),
                    }
                    active_cat = getattr(recommend, "catalog", None) or getattr(
                        getattr(recommend, "recommender", None), "catalog", None
                    )
                    if active_cat:
                        personal_opts = score_personal_options(
                            diner=diner_dict,
                            candidate_options=result.get("options", []),
                            catalog=active_cat,
                        )
                        save_personal_recommendations(
                            db,
                            meal_id=meal_id,
                            user_id=uid,
                            meal_revision=revision,
                            options=personal_opts,
                        )
            notify(
                db,
                included,
                "recommendations_ready",
                "Your meal options are ready to review.",
                current["room_id"],
                meal_id,
            )
            audit(
                db, auth["user_id"], "generation_published", current["room_id"], meal_id
            )
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/personal-recommendations/{rank}/action")
    def personal_action(meal_id: str, rank: int, body: PersonalAction, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], mutable=True, allow_selected=True
            )
            revision_check(meal, body.expected_revision)
            rec = db.execute(
                "SELECT * FROM personal_recommendations WHERE meal_id=? AND user_id=? AND meal_revision=? AND rank=?",
                (meal_id, auth["user_id"], meal["revision"], rank),
            ).fetchone()
            if not rec:
                raise HTTPException(404, "Personal recommendation not found")
            now_stamp = stamp()
            if body.action == "save_backup":
                db.execute(
                    "UPDATE personal_recommendations SET status='saved_backup', updated_at=? WHERE meal_id=? AND user_id=? AND meal_revision=? AND rank=?",
                    (now_stamp, meal_id, auth["user_id"], meal["revision"], rank),
                )
                audit(
                    db,
                    auth["user_id"],
                    "personal_rec_saved_backup",
                    meal["room_id"],
                    meal_id,
                )
            elif body.action == "dismiss":
                db.execute(
                    "UPDATE personal_recommendations SET status='dismissed', updated_at=? WHERE meal_id=? AND user_id=? AND meal_revision=? AND rank=?",
                    (now_stamp, meal_id, auth["user_id"], meal["revision"], rank),
                )
                audit(
                    db,
                    auth["user_id"],
                    "personal_rec_dismissed",
                    meal["room_id"],
                    meal_id,
                )
            elif body.action == "choose_separately":
                db.execute(
                    "UPDATE personal_recommendations SET status='chosen_separately', updated_at=? WHERE meal_id=? AND user_id=? AND meal_revision=? AND rank=?",
                    (now_stamp, meal_id, auth["user_id"], meal["revision"], rank),
                )
                audit(
                    db,
                    auth["user_id"],
                    "personal_rec_chosen_separately",
                    meal["room_id"],
                    meal_id,
                )
                if meal["status"] in {"collecting", "generating", "shortlisted"}:
                    db.execute(
                        "UPDATE participants SET attendance='decline' WHERE meal_id=? AND user_id=?",
                        (meal_id, auth["user_id"]),
                    )
                    db.execute(
                        "UPDATE meals SET revision=revision+1, status='collecting' WHERE id=?",
                        (meal_id,),
                    )
                    invalidate(db, meal_id)
                    meal = db.execute(
                        "SELECT * FROM meals WHERE id=?", (meal_id,)
                    ).fetchone()
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/votes")
    def vote(meal_id: str, body: Vote, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            revision_check(meal, body.expected_revision)
            option_check(meal, body.option_id)
            if auth["user_id"] not in decode(meal["frozen_participants"], []):
                raise HTTPException(
                    403, "You are not included in this recommendation set"
                )
            db.execute(
                "INSERT INTO votes(meal_id,user_id,option_id,revision,approve,choice,reason) VALUES(?,?,?,?,?,?,?) ON CONFLICT(meal_id,user_id,option_id) DO UPDATE SET revision=excluded.revision,approve=excluded.approve,choice=excluded.choice,reason=excluded.reason",
                (
                    meal_id,
                    auth["user_id"],
                    body.option_id,
                    meal["revision"],
                    int(body.choice == "works"),
                    body.choice,
                    body.reason,
                ),
            )
            outlet_id = next(
                (
                    opt.get("outlet_id")
                    for opt in decode(meal["result"], {}).get("options", [])
                    if opt.get("id") == body.option_id
                    or opt.get("option_id") == body.option_id
                ),
                body.option_id,
            )
            policy_version = decode(meal["result"], {}).get("policy_version", "1.0")
            record_exposure_event(
                db,
                EVENT_CANDIDATE_VOTED_ON,
                meal_id=meal_id,
                meal_revision=meal["revision"],
                policy_version=policy_version,
                outlet_id=outlet_id,
                option_id=body.option_id,
                user_id=auth["user_id"],
                metadata={"choice": body.choice, "approved": body.choice == "works"},
            )
            audit(db, auth["user_id"], "private_vote_updated", meal["room_id"], meal_id)
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/delegation")
    def delegate(meal_id: str, body: Delegation, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            revision_check(meal, body.expected_revision)
            if auth["user_id"] not in decode(meal["frozen_participants"], []):
                raise HTTPException(
                    403, "You are not included in this recommendation set"
                )
            options = decode(meal["result"], {}).get("options", [])
            if body.enabled:
                ids = [
                    str(option.get("id", option.get("option_id", "")))
                    for option in options
                ]
                if not ids:
                    raise HTTPException(
                        409, "Generate a checked shortlist before delegating"
                    )
                for option_id in ids:
                    option_check(meal, option_id)
                db.execute(
                    "INSERT INTO delegations VALUES(?,?,?,?,?) ON CONFLICT(meal_id,user_id) DO UPDATE SET revision=excluded.revision,option_ids=excluded.option_ids,created_at=excluded.created_at",
                    (meal_id, auth["user_id"], meal["revision"], encode(ids), stamp()),
                )
            else:
                db.execute(
                    "DELETE FROM delegations WHERE meal_id=? AND user_id=?",
                    (meal_id, auth["user_id"]),
                )
            audit(db, auth["user_id"], "delegation_updated", meal["room_id"], meal_id)
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/select")
    def select(meal_id: str, body: Select, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            revision_check(meal, body.expected_revision)
            included = decode(meal["frozen_participants"], [])
            if len(included) < 2:
                raise HTTPException(
                    409,
                    "Every included participant must approve this option or delegate within this shortlist",
                )
            prior_result = decode(meal["result"], {})
            options = prior_result.get("options", [])
            target_option_id = body.option_id

            if body.choice_mode == "manual":
                if not target_option_id:
                    raise HTTPException(
                        422, "Provide an option_id for manual selection"
                    )
                option_check(meal, target_option_id)
                approvals = approved_users(db, meal, target_option_id)
                if set(included) != approvals:
                    raise HTTPException(
                        409,
                        "Every included participant must approve this option or delegate within this shortlist",
                    )
            elif body.choice_mode in {"tie_break", "random_draw"}:
                accepted_options = [
                    opt
                    for opt in options
                    if approved_users(
                        db, meal, str(opt.get("id", opt.get("option_id", "")))
                    )
                    == set(included)
                ]
                if not accepted_options:
                    raise HTTPException(
                        409,
                        "No option has been approved by every included participant",
                    )
                if body.choice_mode == "tie_break":

                    def tie_break_key(opt):
                        fit_score = opt.get("_score") or opt.get("score") or 0.0
                        dist = opt.get("distance_km", 999.0)
                        ev_count = len(opt.get("evidence", []))
                        venue_id = str(opt.get("outlet_id") or opt.get("id") or "")
                        return (-fit_score, dist, -ev_count, venue_id)

                    best = min(accepted_options, key=tie_break_key)
                    target_option_id = str(best.get("id", best.get("option_id", "")))
                else:
                    import random

                    draw_choice = random.choice(accepted_options)
                    target_option_id = str(
                        draw_choice.get("id", draw_choice.get("option_id", ""))
                    )
            else:
                raise HTTPException(422, f"Unknown choice_mode: {body.choice_mode}")

            snapshot = snapshot_for(db, meal, included)
            prior_result = decode(meal["result"], {})
        try:
            checked = safe_recommend(snapshot, revalidate=True)
        except Exception:  # noqa: BLE001 — the provider boundary must not disclose private exception text
            raise HTTPException(
                503, "The final evidence check is unavailable. Your votes are saved"
            ) from None
        still_valid = (
            checked.get("status") == "shortlisted"
            and any(
                str(o.get("id", o.get("option_id", ""))) == target_option_id
                for o in checked.get("options", [])
            )
            and checked.get("catalog_version") == prior_result.get("catalog_version")
            and checked.get("catalog_id") == prior_result.get("catalog_id")
            and (
                not prior_result.get("evidence_revision")
                or prior_result["evidence_revision"] == evidence_identity(recommend)
            )
        )
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            revision_check(meal, body.expected_revision)
            # Re-read votes after potentially slow external checks; a participant may revoke approval.
            approvals = approved_users(db, meal, target_option_id)
            if approvals != set(included):
                raise HTTPException(409, "An approval changed during the final check")
            if not still_valid or (
                prior_result.get("evidence_revision")
                and prior_result["evidence_revision"] != evidence_identity(recommend)
            ):
                invalidate(db, meal_id)
            else:
                option_check(meal, target_option_id)
                decision = {
                    "option_id": target_option_id,
                    "selected_by": auth["user_id"],
                    "selected_at": stamp(),
                    "revision": meal["revision"],
                    "participant_ids": included,
                    "checked": True,
                    "choice_mode": body.choice_mode,
                }
                db.execute(
                    "UPDATE meals SET status='selected',decision=?,reconfirmation_required=0,lifecycle_reason=NULL WHERE id=?",
                    (encode(decision), meal_id),
                )
                outlet_id = next(
                    (
                        opt.get("outlet_id")
                        for opt in prior_result.get("options", [])
                        if opt.get("id") == target_option_id
                        or opt.get("option_id") == target_option_id
                    ),
                    target_option_id,
                )
                record_exposure_event(
                    db,
                    EVENT_CANDIDATE_SELECTED,
                    meal_id=meal_id,
                    meal_revision=meal["revision"],
                    policy_version=prior_result.get("policy_version", "1.0"),
                    outlet_id=outlet_id,
                    option_id=target_option_id,
                    user_id=auth["user_id"],
                    metadata={"choice_mode": body.choice_mode},
                )
                msg = (
                    "Everyone approved a meal option. A random draw selected the final place."
                    if body.choice_mode == "random_draw"
                    else "Everyone approved a meal option. A score tie-break selected the final place."
                    if body.choice_mode == "tie_break"
                    else "Everyone approved a meal option. View the shared decision."
                )
                notify(
                    db,
                    included,
                    "meal_selected",
                    msg,
                    meal["room_id"],
                    meal_id,
                )
                audit(db, auth["user_id"], "meal_selected", meal["room_id"], meal_id)
                return meal_view(
                    db,
                    db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                    auth["user_id"],
                )
        raise HTTPException(
            409, "Restaurant evidence changed. Review a new recommendation set"
        )

    @router.post("/meals/{meal_id}/manual-plan")
    def propose_manual(meal_id: str, body: ManualPlan, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(
                db, meal_id, auth["user_id"], organizer=True, mutable=True
            )
            revision_check(meal, body.expected_revision)
            if datetime.fromisoformat(decode(meal["payload"])["decision_by"]) <= now():
                raise HTTPException(
                    409, "Extend the decision deadline before proposing a manual plan"
                )
            rows = db.execute(
                "SELECT user_id,attendance FROM participants WHERE meal_id=?",
                (meal_id,),
            ).fetchall()
            joined = {p["user_id"] for p in rows if p["attendance"] == "join"}
            included = (
                set(body.participant_ids)
                if body.participant_ids is not None
                else joined
            )
            if (
                any(p["attendance"] == "pending" for p in rows)
                and not body.exclude_pending
            ):
                raise HTTPException(
                    409, "Wait for pending invitees or explicitly exclude them"
                )
            if (
                not 2 <= len(included) <= 8
                or not included.issubset(joined)
                or auth["user_id"] not in included
            ):
                raise HTTPException(
                    409, "Include 2–8 joined participants including the organizer"
                )
            invalidate(db, meal_id)
            plan = {
                "id": new_id(),
                "name": body.name,
                "address": body.address,
                "note": body.note,
                "unverified": True,
                "requirements_status": "not_verified",
                "notice": "A plan proposed by your group. Restaurant, price and dietary checks remain unresolved; acknowledgements do not establish suitability.",
            }
            db.execute(
                "UPDATE meals SET status='manual_proposed',manual_plan=?,frozen_participants=? WHERE id=?",
                (encode(plan), encode(sorted(included)), meal_id),
            )
            notify(
                db,
                included,
                "manual_plan_proposed",
                "Your group proposed an unverified plan. Review the unresolved checks before acknowledging.",
                meal["room_id"],
                meal_id,
            )
            audit(db, auth["user_id"], "manual_plan_proposed", meal["room_id"], meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    def manual_scope(db, meal_id, expected, auth, organizer=False):
        meal = meal_member(
            db, meal_id, auth["user_id"], organizer=organizer, mutable=True
        )
        revision_check(meal, expected)
        if meal["status"] != "manual_proposed" or not meal["manual_plan"]:
            raise HTTPException(409, "Review the current manual plan first")
        included = set(decode(meal["frozen_participants"], []))
        if auth["user_id"] not in included:
            raise HTTPException(403, "You are not included in this manual plan")
        return meal, included

    @router.post("/meals/{meal_id}/manual-ack")
    def acknowledge_manual(meal_id: str, body: ManualAcknowledgement, auth=Auth):
        with store.transaction() as db:
            meal, _ = manual_scope(db, meal_id, body.expected_revision, auth)
            if body.acknowledge_unverified:
                db.execute(
                    "INSERT INTO manual_acknowledgements VALUES(?,?,?,?) ON CONFLICT(meal_id,user_id) DO UPDATE SET revision=excluded.revision,created_at=excluded.created_at",
                    (meal_id, auth["user_id"], meal["revision"], stamp()),
                )
            else:
                db.execute(
                    "DELETE FROM manual_acknowledgements WHERE meal_id=? AND user_id=?",
                    (meal_id, auth["user_id"]),
                )
            audit(
                db,
                auth["user_id"],
                "manual_plan_acknowledgement",
                meal["room_id"],
                meal_id,
            )
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/manual-select")
    def select_manual(meal_id: str, body: Cancel, auth=Auth):
        with store.transaction() as db:
            meal, included = manual_scope(
                db, meal_id, body.expected_revision, auth, organizer=True
            )
            acknowledged = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM manual_acknowledgements WHERE meal_id=? AND revision=?",
                    (meal_id, meal["revision"]),
                )
            }
            if len(included) < 2 or included != acknowledged:
                raise HTTPException(
                    409,
                    "Every included participant must acknowledge the unresolved checks for this exact plan",
                )
            plan = decode(meal["manual_plan"])
            decision = {
                "option_id": plan["id"],
                "selected_by": auth["user_id"],
                "selected_at": stamp(),
                "revision": meal["revision"],
                "participant_ids": sorted(included),
                "checked": False,
                "choice_mode": "manual_unverified",
                "requirements_status": "not_verified",
            }
            db.execute(
                "UPDATE meals SET status='manual_selected',decision=?,reconfirmation_required=0 WHERE id=?",
                (encode(decision), meal_id),
            )
            notify(
                db,
                included,
                "manual_plan_recorded",
                "Your group recorded an unverified plan. Restaurant checks remain unresolved.",
                meal["room_id"],
                meal_id,
            )
            audit(
                db, auth["user_id"], "manual_choice_recorded", meal["room_id"], meal_id
            )
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/feedback")
    def feedback(meal_id: str, body: Feedback, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"])
            decision = decode(meal["decision"], {})
            if (
                meal["status"] not in POST_DECISION_STATUSES
                or auth["user_id"] not in decision.get("participant_ids", [])
                or body.option_id != decision.get("option_id")
            ):
                raise HTTPException(
                    409, "Feedback must refer to your selected meal option"
                )
            if (
                body.visited
                and datetime.fromisoformat(decode(meal["payload"])["meal_at"])
                + timedelta(
                    minutes=decode(meal["payload"]).get(
                        "duration_minutes", DEFAULT_MEAL_MINUTES
                    )
                )
                > now()
            ):
                raise HTTPException(
                    409, "Confirm your visit after the planned meal finishes"
                )
            payload = body.model_dump()
            db.execute(
                "INSERT INTO feedback VALUES(?,?,?,?) ON CONFLICT(meal_id,user_id) DO UPDATE SET payload=excluded.payload,created_at=excluded.created_at",
                (meal_id, auth["user_id"], encode(payload), stamp()),
            )
            profile = decode(
                db.execute(
                    "SELECT profile FROM users WHERE id=?", (auth["user_id"],)
                ).fetchone()["profile"]
            )
            previous_observations = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM observations WHERE user_id=? AND meal_id=?",
                    (auth["user_id"], meal_id),
                )
            ]
            for observation in previous_observations:
                remove_observation(db, auth["user_id"], observation)

            if (
                profile.get("memory_enabled")
                and decision.get("checked", meal["result"] is not None) is True
                and body.visited
                and (
                    body.rating is not None
                    or body.would_repeat is not None
                    or body.enjoyment not in {None, "skipped"}
                )
            ):
                # Observations never rewrite confirmed profile requirements or preferences.
                option = next(
                    (
                        o
                        for o in decode(meal["result"], {}).get("options", [])
                        if str(o.get("id", o.get("option_id"))) == body.option_id
                    ),
                    {},
                )
                attr_signals = extract_attribute_signals(body.model_dump())
                observation_id = new_id()
                db.execute(
                    "INSERT INTO observations VALUES(?,?,?,?,?)",
                    (
                        observation_id,
                        auth["user_id"],
                        meal_id,
                        encode(
                            {
                                "outlet_id": option.get("outlet_id"),
                                "rating": body.rating,
                                "would_repeat": body.would_repeat,
                                "source": "confirmed_visit",
                                "enjoyment": body.enjoyment,
                                "influences": body.influences,
                                "cost_expectation": body.cost_expectation,
                                "actual_cost_minor": body.actual_cost_minor,
                                "dish_text": body.dish_text,
                                "fairness": body.fairness,
                                "attribute_signals": attr_signals,
                            }
                        ),
                        stamp(),
                    ),
                )
                if body.would_repeat is not None and option.get("outlet_id"):
                    propose_repeat_pattern(db, auth["user_id"], option["outlet_id"])
                propose_attribute_patterns(db, auth["user_id"], observation_id)
            # Feedback changes personal taste evidence, never another person's constraints.
            for upcoming in db.execute(
                "SELECT meal_id FROM participants WHERE user_id=? AND meal_id!=?",
                (auth["user_id"], meal_id),
            ).fetchall():
                invalidate(db, upcoming["meal_id"])
            audit(
                db, auth["user_id"], "private_feedback_saved", meal["room_id"], meal_id
            )
            return meal_view(db, meal, auth["user_id"])

    @router.post("/meals/{meal_id}/report-data-error")
    def report_data_error(meal_id: str, body: DataErrorReport, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"])
            report_id = f"rpt_{secrets.token_hex(8)}"
            now_stamp = stamp()
            db.execute(
                "INSERT INTO data_error_reports(id,meal_id,user_id,outlet_id,category,description,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    report_id,
                    meal_id,
                    auth["user_id"],
                    body.outlet_id,
                    body.category,
                    body.description,
                    "investigating",
                    now_stamp,
                ),
            )
            audit(db, auth["user_id"], "data_error_reported", meal["room_id"], meal_id)
            return {
                "report_id": report_id,
                "status": "investigating",
                "message": "Your report was logged for operator investigation. It is kept separate from taste learning.",
                "created_at": now_stamp,
            }
