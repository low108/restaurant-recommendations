"""Meal routes: create and edit a meal, participants, responses, origins and confirmations."""

from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from dining.api.common import (
    new_id,
    now,
    stamp,
)
from dining.api.context import ApiContext
from dining.api.schemas import (
    Cancel,
    CheckIn,
    InterpretPreferences,
    MealCreate,
    MealOriginInput,
    ParticipantSet,
    PreparationConfirmationInput,
)
from dining.core.constants import LOCAL_TIMEZONE
from dining.core.store import decode, encode
from dining.llm.inference import InferenceSettings
from dining.llm.preferences import interpret_preferences
from dining.location.geocoding import get_geocoding_provider
from dining.meals.exposure import (
    record_card_impressions,
)


def register(router: APIRouter, ctx: ApiContext) -> None:
    """Add this module's routes to ``router``."""
    # Shared helpers, bound to local names so the route bodies read naturally.
    Auth = ctx.Auth
    audit = ctx.audit
    authenticate = ctx.authenticate
    demo_mode = ctx.demo_mode
    invalidate = ctx.invalidate
    meal_member = ctx.meal_member
    meal_view = ctx.meal_view
    notify = ctx.notify
    recommend = ctx.recommend
    revision_check = ctx.revision_check
    room_member = ctx.room_member
    store = ctx.store

    @router.post("/rooms/{room_id}/meals", status_code=201)
    def create_meal(room_id: str, body: MealCreate, auth=Auth):
        with store.transaction() as db:
            room_member(db, room_id, auth["user_id"], active=True)
            existing = db.execute(
                "SELECT * FROM meals WHERE room_id=? AND organizer_id=? AND idempotency_key=?",
                (room_id, auth["user_id"], body.idempotency_key),
            ).fetchone()
            if existing:
                return meal_view(db, existing, auth["user_id"])
            members = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM members WHERE room_id=?", (room_id,)
                )
            }
            invited = (
                set(body.participant_ids)
                if body.participant_ids is not None
                else members
            )
            if (
                not 2 <= len(invited) <= 8
                or not invited.issubset(members)
                or auth["user_id"] not in invited
            ):
                raise HTTPException(
                    422, "Invite 2–8 current room members including yourself"
                )
            meal_id = new_id()
            payload = body.model_dump(
                mode="json",
                exclude={"participant_ids", "idempotency_key", "deadline_minutes"},
            )
            payload["timezone"] = LOCAL_TIMEZONE
            if demo_mode:
                payload["synthetic"] = True
            db.execute(
                "INSERT INTO meals(id,room_id,organizer_id,payload,created_at,idempotency_key,original_answer_by,original_decision_by,original_invited_count) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    meal_id,
                    room_id,
                    auth["user_id"],
                    encode(payload),
                    stamp(),
                    body.idempotency_key,
                    payload["answer_by"],
                    payload["decision_by"],
                    len(invited),
                ),
            )
            db.executemany(
                "INSERT INTO participants(meal_id,user_id) VALUES(?,?)",
                [(meal_id, uid) for uid in invited],
            )
            notify(
                db,
                invited,
                "meal_invitation",
                "A meal is open for your private check-in.",
                room_id,
                meal_id,
            )
            audit(db, auth["user_id"], "meal_created", room_id, meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.get("/locations/neighbourhoods")
    def lookup_neighbourhoods(query: str = ""):
        provider = get_geocoding_provider()
        res = provider.lookup_neighbourhood(query)
        return res.to_dict()

    @router.get("/locations/reverse")
    def reverse_lookup_location(latitude: float, longitude: float):
        provider = get_geocoding_provider()
        res = provider.reverse_lookup(latitude, longitude)
        return res.to_dict()

    @router.get("/meals/{meal_id}")
    def get_meal(meal_id: str, auth=Auth):
        with store.transaction() as db:
            view = meal_view(
                db, meal_member(db, meal_id, auth["user_id"]), auth["user_id"]
            )
            if view.get("result") and view["result"].get("options"):
                record_card_impressions(
                    db,
                    meal_id=view["id"],
                    meal_revision=view["revision"],
                    policy_version=view["result"].get("policy_version", "1.0"),
                    user_id=auth["user_id"],
                    options=view["result"]["options"],
                )
            return view

    @router.get("/meals/{meal_id}/recommendation-history")
    def recommendation_history(meal_id: str, auth=Auth):
        with store.transaction() as db:
            meal_member(db, meal_id, auth["user_id"])
            rows = db.execute(
                "SELECT revision,payload,created_at FROM recommendation_archives WHERE meal_id=? ORDER BY revision DESC",
                (meal_id,),
            ).fetchall()
            return [
                {
                    "revision": r["revision"],
                    "created_at": r["created_at"],
                    "result": decode(r["payload"]),
                }
                for r in rows
            ]

    @router.patch("/meals/{meal_id}")
    def edit_meal(meal_id: str, body: dict, auth=Auth):
        fields = {
            "kind",
            "meal_at",
            "answer_by",
            "decision_by",
            "duration_minutes",
            "location_label",
            "latitude",
            "longitude",
            "radius_km",
        }
        if not body or set(body) - fields - {"expected_revision"}:
            raise HTTPException(422, "Provide supported meal fields")
        with store.transaction() as db:
            meal = meal_member(
                db,
                meal_id,
                auth["user_id"],
                organizer=True,
                mutable=True,
                allow_selected=True,
            )
            revision_check(meal, body.get("expected_revision"))
            values = decode(meal["payload"])
            values.pop("timezone", None)
            values.pop("synthetic", None)
            values.update({k: v for k, v in body.items() if k in fields})
            values["idempotency_key"] = meal["idempotency_key"]
            try:
                validated = MealCreate.model_validate(values)
            except ValueError as error:
                raise HTTPException(
                    422, "Check meal coordinates and future deadline order"
                ) from error
            payload = validated.model_dump(
                mode="json",
                exclude={"participant_ids", "idempotency_key", "deadline_minutes"},
            )
            payload["timezone"] = LOCAL_TIMEZONE
            if demo_mode:
                payload["synthetic"] = True
            db.execute(
                "UPDATE meals SET payload=? WHERE id=?", (encode(payload), meal_id)
            )
            invalidate(db, meal_id)
            # Changed logistics require every participant to actively reconfirm.
            db.execute(
                "UPDATE participants SET response_revision=response_revision+1,response=json_set(response,'$.ready',json('false')) WHERE meal_id=? AND response IS NOT NULL",
                (meal_id,),
            )
            users = [
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM participants WHERE meal_id=?", (meal_id,)
                )
            ]
            notify(
                db,
                users,
                "meal_changed",
                "The meal details changed. Please review your check-in.",
                meal["room_id"],
                meal_id,
            )
            audit(db, auth["user_id"], "meal_edited", meal["room_id"], meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/cancel")
    def cancel_meal(meal_id: str, body: Cancel, auth=Auth):
        with store.transaction() as db:
            # Future decisions can be withdrawn explicitly. Completed history cannot.
            meal = meal_member(db, meal_id, auth["user_id"], organizer=True)
            room_member(db, meal["room_id"], auth["user_id"], active=True)
            revision_check(meal, body.expected_revision)
            if meal["status"] == "cancelled":
                raise HTTPException(409, "This meal is already cancelled")
            if datetime.fromisoformat(decode(meal["payload"])["meal_at"]) <= now():
                raise HTTPException(
                    409,
                    "Past meals remain in history; record what happened through feedback",
                )
            db.execute(
                "UPDATE meals SET status='cancelled',revision=revision+1,result=NULL,result_revision=NULL,frozen_participants=NULL,decision=NULL,manual_plan=NULL WHERE id=?",
                (meal_id,),
            )
            db.execute("DELETE FROM votes WHERE meal_id=?", (meal_id,))
            db.execute("DELETE FROM delegations WHERE meal_id=?", (meal_id,))
            db.execute(
                "DELETE FROM manual_acknowledgements WHERE meal_id=?", (meal_id,)
            )
            users = [
                row["user_id"]
                for row in db.execute(
                    "SELECT user_id FROM participants WHERE meal_id=?", (meal_id,)
                )
            ]
            notify(
                db,
                users,
                "meal_cancelled",
                "The organizer cancelled this meal.",
                meal["room_id"],
                meal_id,
            )
            audit(db, auth["user_id"], "meal_cancelled", meal["room_id"], meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/participants")
    def edit_participants(meal_id: str, body: ParticipantSet, auth=Auth):
        selected = set(body.participant_ids)
        if len(selected) != len(body.participant_ids):
            raise HTTPException(422, "Participant IDs must be unique")
        with store.transaction() as db:
            meal = meal_member(
                db,
                meal_id,
                auth["user_id"],
                organizer=True,
                mutable=True,
                allow_selected=True,
            )
            revision_check(meal, body.expected_revision)
            members = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM members WHERE room_id=?", (meal["room_id"],)
                )
            }
            if not selected.issubset(members) or auth["user_id"] not in selected:
                raise HTTPException(422, "Select room members including the organizer")
            current = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM participants WHERE meal_id=?", (meal_id,)
                )
            }
            for uid in current - selected:
                db.execute(
                    "DELETE FROM participants WHERE meal_id=? AND user_id=?",
                    (meal_id, uid),
                )
            for uid in selected - current:
                db.execute(
                    "INSERT INTO participants(meal_id,user_id) VALUES(?,?)",
                    (meal_id, uid),
                )
            invalidate(db, meal_id)
            notify(
                db,
                current | selected,
                "participants_changed",
                "The meal participant list changed. Review the current list.",
                meal["room_id"],
                meal_id,
            )
            audit(
                db,
                auth["user_id"],
                "meal_participants_changed",
                meal["room_id"],
                meal_id,
            )
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.post("/meals/{meal_id}/interpret-preferences")
    def interpret_craving(
        meal_id: str, body: InterpretPreferences, request: Request, auth=Auth
    ):
        def current(db):
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            if (
                meal["decision"]
                or datetime.fromisoformat(decode(meal["payload"])["answer_by"]) <= now()
            ):
                raise HTTPException(
                    409, "This meal no longer accepts preference drafts"
                )
            person = db.execute(
                "SELECT response_revision FROM participants WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if not person:
                raise HTTPException(404, "Meal not found")
            if (
                meal["revision"] != body.expected_revision
                or person["response_revision"] != body.expected_response_revision
            ):
                raise HTTPException(
                    409,
                    "The meal or your answer changed. Refresh before interpreting again",
                )

        with store.transaction() as db:
            current(db)
            count = db.execute(
                "SELECT COUNT(*) FROM preference_requests WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()[0]
            recent = db.execute(
                "SELECT COUNT(*) FROM preference_requests WHERE user_id=? AND requested_at>?",
                (auth["user_id"], time.time() - 3600),
            ).fetchone()[0]
            if count >= 2 or recent >= 10:
                raise HTTPException(
                    429,
                    "AI draft limit reached. You can still edit and submit your preferences manually",
                )
            request_id = new_id()
            db.execute(
                "INSERT INTO preference_requests(id,meal_id,user_id,requested_at,status) VALUES(?,?,?,?,?)",
                (request_id, meal_id, auth["user_id"], time.time(), "requested"),
            )
        # Never hold the database lock across model I/O. Nothing here saves answers.
        settings = getattr(recommend, "settings", InferenceSettings())
        result = interpret_preferences(settings, body.text)
        authenticate(
            request
        )  # Logout/deletion while the provider ran revokes return access.
        with store.transaction() as db:
            current(db)
            db.execute(
                "UPDATE preference_requests SET status=?,metadata=? WHERE id=?",
                (result["status"], encode(result["agent"]), request_id),
            )
        return {
            **result,
            "request_id": request_id,
            "remaining_requests": max(0, 1 - count),
        }

    @router.put("/meals/{meal_id}/response")
    def checkin(meal_id: str, body: CheckIn, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            if datetime.fromisoformat(decode(meal["payload"])["answer_by"]) <= now():
                raise HTTPException(
                    409, "The check-in deadline passed. Ask the organizer to extend it"
                )
            profile = decode(
                db.execute(
                    "SELECT profile FROM users WHERE id=?", (auth["user_id"],)
                ).fetchone()["profile"]
            )
            if body.ready and not profile.get("requirements_reviewed"):
                raise HTTPException(
                    422, "Review your private profile requirements first"
                )
            previous = db.execute(
                "SELECT response_revision,response FROM participants WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if previous["response_revision"] != body.expected_response_revision:
                raise HTTPException(
                    409,
                    {
                        "code": "RESPONSE_CHANGED",
                        "message": "Your answer changed in another tab. Review the current answer before saving.",
                        "current_response_revision": previous["response_revision"],
                        "current_response": decode(previous["response"]),
                    },
                )
            data = body.model_dump(exclude={"expected_response_revision"})
            if body.budget is not None:
                data["budget_minor"] = round(body.budget * 100)
            db.execute(
                "UPDATE participants SET attendance=?,response=?,response_revision=response_revision+1 WHERE meal_id=? AND user_id=?",
                (body.attendance, encode(data), meal_id, auth["user_id"]),
            )
            invalidate(db, meal_id)
            audit(db, auth["user_id"], "checkin_updated", meal["room_id"], meal_id)
            return meal_view(
                db,
                db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                auth["user_id"],
            )

    @router.put("/meals/{meal_id}/origin")
    def set_meal_origin(meal_id: str, body: MealOriginInput, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            participant = db.execute(
                "SELECT * FROM participants WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if not participant:
                raise HTTPException(404, "Participant record not found")
            now_iso = stamp()
            db.execute(
                """
                INSERT INTO meal_origins(
                    meal_id, user_id, origin_mode, latitude, longitude,
                    approximate_area, route_consent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(meal_id, user_id) DO UPDATE SET
                    origin_mode=excluded.origin_mode,
                    latitude=excluded.latitude,
                    longitude=excluded.longitude,
                    approximate_area=excluded.approximate_area,
                    route_consent=excluded.route_consent,
                    updated_at=excluded.updated_at
                """,
                (
                    meal_id,
                    auth["user_id"],
                    body.origin_mode,
                    body.latitude,
                    body.longitude,
                    body.approximate_area,
                    1 if body.route_consent else 0,
                    now_iso,
                    now_iso,
                ),
            )
            db.execute(
                "UPDATE participants SET response_revision=response_revision+1 WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            )
            invalidate(db, meal_id)
            audit(db, auth["user_id"], "meal_origin_updated", meal["room_id"], meal_id)
            return {
                "meal_id": meal_id,
                "origin_mode": body.origin_mode,
                "latitude": body.latitude,
                "longitude": body.longitude,
                "approximate_area": body.approximate_area,
                "route_consent": body.route_consent,
                "updated_at": now_iso,
            }

    @router.get("/meals/{meal_id}/origin")
    def get_meal_origin(meal_id: str, auth=Auth):
        with store.transaction() as db:
            meal_member(db, meal_id, auth["user_id"])
            row = db.execute(
                "SELECT * FROM meal_origins WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if not row:
                return None
            return {
                "meal_id": meal_id,
                "origin_mode": row["origin_mode"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "approximate_area": row["approximate_area"],
                "route_consent": bool(row["route_consent"]),
                "updated_at": row["updated_at"],
            }

    @router.delete("/meals/{meal_id}/origin")
    def delete_meal_origin(meal_id: str, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            db.execute(
                "DELETE FROM meal_origins WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            )
            db.execute(
                "UPDATE participants SET response_revision=response_revision+1 WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            )
            invalidate(db, meal_id)
            audit(db, auth["user_id"], "meal_origin_deleted", meal["room_id"], meal_id)
            return {"deleted": True}

    @router.post("/meals/{meal_id}/preparation-confirmations", status_code=201)
    def add_preparation_confirmation(
        meal_id: str, body: PreparationConfirmationInput, auth=Auth
    ):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            conf_id = str(uuid.uuid4())
            now_iso = stamp()
            db.execute(
                """
                INSERT INTO preparation_confirmations(
                    id, meal_id, outlet_id, requirement_category, confirmed_by,
                    confirmation_channel, confirmed_at, expires_at, exact_bounded_claim, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conf_id,
                    meal_id,
                    body.outlet_id,
                    body.requirement_category,
                    body.confirmed_by,
                    body.confirmation_channel,
                    body.confirmed_at,
                    body.expires_at,
                    body.exact_bounded_claim,
                    now_iso,
                ),
            )
            invalidate(db, meal_id)
            audit(
                db, auth["user_id"], "preparation_confirmed", meal["room_id"], meal_id
            )
            return {
                "id": conf_id,
                "meal_id": meal_id,
                "outlet_id": body.outlet_id,
                "requirement_category": body.requirement_category,
                "confirmed_by": body.confirmed_by,
                "confirmation_channel": body.confirmation_channel,
                "confirmed_at": body.confirmed_at,
                "expires_at": body.expires_at,
                "exact_bounded_claim": body.exact_bounded_claim,
                "created_at": now_iso,
            }

    @router.get("/meals/{meal_id}/preparation-confirmations")
    def list_preparation_confirmations(meal_id: str, auth=Auth):
        with store.transaction() as db:
            meal_member(db, meal_id, auth["user_id"])
            rows = db.execute(
                "SELECT * FROM preparation_confirmations WHERE meal_id=? ORDER BY created_at",
                (meal_id,),
            ).fetchall()
            return [
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
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    @router.delete("/meals/{meal_id}/preparation-confirmations/{confirmation_id}")
    def delete_preparation_confirmation(meal_id: str, confirmation_id: str, auth=Auth):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            db.execute(
                "DELETE FROM preparation_confirmations WHERE id=? AND meal_id=?",
                (confirmation_id, meal_id),
            )
            invalidate(db, meal_id)
            audit(
                db,
                auth["user_id"],
                "preparation_confirmation_deleted",
                meal["room_id"],
                meal_id,
            )
            return {"deleted": True}
