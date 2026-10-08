"""Recommendation generation behind ``POST /meals/{meal_id}/generate``.

:class:`GenerationPipeline` freezes participants into a snapshot, calls the recommender
through a privacy filter, and publishes the result. The :class:`GenerationWorker` drives
these three steps (synchronously or on a background thread) and discards any job whose
meal changed in the meantime.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

from dining.api.common import now, stamp
from dining.api.context import ApiContext
from dining.core.constants import (
    RESULT_STATUSES,
    UNDISCLOSED_ALLERGY_STATUSES,
    UNREVIEWED_HALAL_POLICIES,
)
from dining.core.store import decode, encode
from dining.meals.exposure import (
    record_generation_exposures,
)
from dining.meals.generation import (
    GenerationWorker,
    Superseded,
    evidence_identity,
    policy_identity,
    snapshot_identity,
)
from dining.recommendation.personal import (
    save_personal_recommendations,
    score_personal_options,
)


class GenerationPipeline:
    """Snapshot → recommend → publish, bound to one :class:`ApiContext`."""

    def __init__(self, ctx: ApiContext):
        self.ctx = ctx
        self.worker = GenerationWorker(
            self.ctx.store,
            self.prepare_generation,
            self.safe_recommend,
            self.publish_generation,
            **(self.ctx.generation_options or {}),
        )
        ctx.generation = self
        ctx.generation_worker = self.worker

    def snapshot_for(self, db, meal, included):
        """Freeze the participants' current profiles and responses for one generation run."""
        participants = []
        for uid in included:
            row = db.execute(
                "SELECT p.*,u.profile,u.profile_revision FROM participants p JOIN users u ON u.id=p.user_id JOIN members mb ON mb.user_id=p.user_id AND mb.room_id=? WHERE p.meal_id=? AND p.user_id=?",
                (meal["room_id"], meal["id"], uid),
            ).fetchone()
            if not row or row["attendance"] != "join":
                raise HTTPException(
                    409, "A participant is no longer included. Refresh this meal"
                )
            profile, response = decode(row["profile"]), decode(row["response"], {})
            if not response.get("ready") or not response.get("requirements_confirmed"):
                raise HTTPException(
                    409,
                    "Every included participant must finish and confirm their check-in",
                )
            person = {
                "user_id": uid,
                "profile": profile,
                "response": response,
                "profile_revision": row["profile_revision"],
                "response_revision": row["response_revision"],
            }
            origin_row = db.execute(
                "SELECT * FROM meal_origins WHERE meal_id=? AND user_id=?",
                (meal["id"], uid),
            ).fetchone()
            if origin_row:
                person["origin"] = {
                    "origin_mode": origin_row["origin_mode"],
                    "latitude": origin_row["latitude"],
                    "longitude": origin_row["longitude"],
                    "approximate_area": origin_row["approximate_area"],
                    "route_consent": bool(origin_row["route_consent"]),
                }
            else:
                person["origin"] = {
                    "origin_mode": "do_not_use",
                    "latitude": None,
                    "longitude": None,
                    "approximate_area": None,
                    "route_consent": False,
                }
            if profile.get("memory_enabled"):
                person["venue_preferences"] = [
                    {
                        "outlet_id": r["outlet_id"],
                        "would_repeat": bool(r["would_repeat"]),
                        "source_observation_id": r["observation_id"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM preference_proposals WHERE user_id=? AND status='accepted' AND (proposal_type='venue' OR proposal_type IS NULL) ORDER BY reviewed_at DESC",
                        (uid,),
                    )
                ]
                person["attribute_preferences"] = [
                    {
                        "attribute": r["attribute"],
                        "proposed_value": r["proposed_value"],
                        "description": r["description"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM preference_proposals WHERE user_id=? AND status='accepted' AND proposal_type='attribute' ORDER BY reviewed_at DESC",
                        (uid,),
                    )
                ]
                person["observations"] = [
                    {
                        **decode(observation["payload"]),
                        "created_at": observation["created_at"],
                    }
                    for observation in db.execute(
                        "SELECT payload,created_at FROM observations WHERE user_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 20",
                        (uid, (now() - timedelta(days=90)).isoformat()),
                    )
                ]
            participants.append(person)
        # Shared history contains confirmed occurrences, not named individual ratings.
        history = []
        for old in db.execute(
            "SELECT m.id,m.decision,m.result,m.payload,m.created_at FROM meals m WHERE m.room_id=? AND m.status IN ('selected','manual_selected','awaiting_feedback','closed') ORDER BY m.created_at DESC LIMIT 20",
            (meal["room_id"],),
        ):
            visited = db.execute(
                "SELECT 1 FROM feedback WHERE meal_id=? AND json_extract(payload,'$.visited')=1 LIMIT 1",
                (old["id"],),
            ).fetchone()
            decision = decode(old["decision"], {})
            option = next(
                (
                    o
                    for o in decode(old["result"], {}).get("options", [])
                    if str(o.get("id", o.get("option_id"))) == decision.get("option_id")
                ),
                {},
            )
            history.append(
                {
                    "meal_id": old["id"],
                    "outlet_id": option.get("outlet_id"),
                    "visited": bool(visited),
                    "created_at": old["created_at"],
                    "meal_at": decode(old["payload"])["meal_at"],
                }
            )
        confirmations = [
            {
                "id": r["id"],
                "meal_id": r["meal_id"],
                "outlet_id": r["outlet_id"],
                "requirement_category": r["requirement_category"],
                "confirmed_by": r["confirmed_by"],
                "confirmation_channel": r["confirmation_channel"],
                "confirmed_at": r["confirmed_at"],
                "expires_at": r["expires_at"],
                "exact_bounded_claim": r["exact_bounded_claim"],
            }
            for r in db.execute(
                "SELECT * FROM preparation_confirmations WHERE meal_id=?",
                (meal["id"],),
            )
        ]
        return {
            "meal": {
                **decode(meal["payload"]),
                "id": meal["id"],
                "room_id": meal["room_id"],
                "revision": meal["revision"],
            },
            "participants": participants,
            "history": history,
            "preparation_confirmations": confirmations,
            "revision": meal["revision"],
        }

    def safe_recommend(self, snapshot, revalidate=False):
        """Call the recommender and keep only the public result fields.

        Requirements that are unknown or unreviewed stop the run before the recommender is called.
        """
        # Defense in depth: even a mistaken tool callback cannot treat undisclosed requirements as satisfied.
        if any(
            p["profile"].get("allergy_status") in UNDISCLOSED_ALLERGY_STATUSES
            or p["profile"].get("halal_policy") in UNREVIEWED_HALAL_POLICIES
            or not p["profile"].get("requirements_reviewed")
            for p in snapshot["participants"]
        ):
            return {
                "status": "needs_verification",
                "options": [],
                "verification": [],
                "explanation": "Some private requirements need clarification before suitability can be checked.",
            }
        worker = (
            getattr(self.ctx.recommend, "revalidate", self.ctx.recommend)
            if revalidate
            else self.ctx.recommend
        )
        result = worker(snapshot)
        if (
            not isinstance(result, dict)
            or result.get("status") not in RESULT_STATUSES
            or not isinstance(result.get("options", []), list)
        ):
            raise ValueError("Invalid recommendation output")
        # The deterministic recommendation service returns a purpose-limited public result.
        allowed = {
            "status",
            "options",
            "verification",
            "explanation",
            "catalog_id",
            "catalog_version",
            "synthetic",
            "policy_version",
            "feature_version",
            "ontology_version",
            "retrieval_status",
            "retrieval_policy_version",
            "embedding_model",
            "retrieval_receipt",
            "examined_outlets",
            "coverage",
            "metrics",
            "run_id",
            "agent",
            "_exposure_candidates",
        }
        return {k: v for k, v in result.items() if k in allowed}

    def prepare_generation(self, db, job):
        """Re-check a queued job is still current and return its input snapshot.

        Any change since the job was queued raises :class:`Superseded`.
        """
        meal = db.execute(
            "SELECT * FROM meals WHERE id=?", (job["meal_id"],)
        ).fetchone()
        if (
            not meal
            or meal["revision"] != job["context_revision"]
            or meal["status"] != "generating"
        ):
            raise Superseded()
        try:
            meal = self.ctx.meal_member(
                db, meal["id"], meal["organizer_id"], organizer=True, mutable=True
            )
            room = self.ctx.room_member(
                db, meal["room_id"], meal["organizer_id"], active=True
            )
            if (
                room["membership_revision"] != job["membership_revision"]
                or datetime.fromisoformat(decode(meal["payload"])["decision_by"])
                <= now()
            ):
                raise Superseded()
            inputs = db.execute(
                "SELECT * FROM generation_inputs WHERE job_id=? ORDER BY user_id",
                (job["id"],),
            ).fetchall()
            included = [row["user_id"] for row in inputs]
            if len(included) < 2 or included != sorted(
                decode(meal["frozen_participants"], [])
            ):
                raise Superseded()
            snapshot = self.snapshot_for(db, meal, included)
            if any(
                person["profile_revision"] != old["profile_revision"]
                or person["response_revision"] != old["response_revision"]
                for person, old in zip(snapshot["participants"], inputs, strict=True)
            ):
                raise Superseded()
        except HTTPException:
            raise Superseded() from None
        if (
            job["evidence_revision"] != evidence_identity(self.ctx.recommend)
            or job["policy_version"] != policy_identity()
        ):
            raise Superseded()
        if job["input_fingerprint"] is not None and job[
            "input_fingerprint"
        ] != snapshot_identity(snapshot):
            raise Superseded()
        return snapshot

    def publish_generation(self, db, job, result):
        """Store the shortlist, personal picks, notices and audit entry in one transaction."""
        # Result publication, the terminal attempt and inbox notices commit together.
        result = {
            **result,
            "generation_job_id": job["id"],
            "evidence_revision": job["evidence_revision"],
        }
        meal = db.execute(
            "SELECT * FROM meals WHERE id=?", (job["meal_id"],)
        ).fetchone()
        db.execute(
            "UPDATE meals SET status=?,result=?,result_revision=? WHERE id=?",
            (result["status"], encode(result), job["context_revision"], job["meal_id"]),
        )
        db.execute(
            "INSERT OR REPLACE INTO recommendation_archives(id,meal_id,revision,payload,created_at) VALUES(?,?,?,?,?)",
            (
                f"{job['meal_id']}:{job['context_revision']}",
                job["meal_id"],
                job["context_revision"],
                encode(result),
                stamp(),
            ),
        )
        record_generation_exposures(db, job["meal_id"], job["context_revision"], result)
        for uid in decode(meal["frozen_participants"], []):
            p_row = db.execute(
                "SELECT p.*, u.profile FROM participants p JOIN users u ON u.id=p.user_id WHERE p.meal_id=? AND p.user_id=?",
                (job["meal_id"], uid),
            ).fetchone()
            if p_row:
                diner_dict = {
                    "user_id": uid,
                    "profile": decode(p_row["profile"], {}),
                    "response": decode(p_row["response"], {}),
                }
                active_cat = getattr(self.ctx.recommend, "catalog", None) or getattr(
                    getattr(self.ctx.recommend, "recommender", None), "catalog", None
                )
                if active_cat:
                    personal_opts = score_personal_options(
                        diner=diner_dict,
                        candidate_options=result.get("options", []),
                        catalog=active_cat,
                    )
                    save_personal_recommendations(
                        db,
                        meal_id=job["meal_id"],
                        user_id=uid,
                        meal_revision=job["context_revision"],
                        options=personal_opts,
                    )
        self.ctx.notify(
            db,
            decode(meal["frozen_participants"], []),
            "recommendations_ready",
            "Your meal options are ready to review.",
            meal["room_id"],
            meal["id"],
        )
        self.ctx.audit(
            db,
            meal["organizer_id"],
            "generation_published",
            meal["room_id"],
            meal["id"],
        )
