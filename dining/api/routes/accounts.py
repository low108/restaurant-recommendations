"""Account routes: session, sign-in, profile, consent, push, inbox, export and deletion."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response

from dining.accounts.consent import ConsentManager
from dining.api.common import (
    COOKIE,
    DUMMY_PASSWORD_HASH,
    PASSWORD_HASHER,
    new_id,
    stamp,
)
from dining.api.context import ApiContext
from dining.api.routes.learning import learning_view
from dining.api.schemas import (
    ConsentInput,
    Credentials,
    Profile,
    PushSubscriptionInput,
    PushUnsubscribeInput,
    Registration,
)
from dining.core.store import DEFAULT_PROFILE, decode, encode
from dining.notifications.push import PushSubscriptionManager


def register(router: APIRouter, ctx: ApiContext) -> None:
    """Add this module's routes to ``router``."""
    # Shared helpers, bound to local names so the route bodies read naturally.
    Auth = ctx.Auth
    audit = ctx.audit
    authenticate = ctx.authenticate
    cancel_upcoming_room_meals = ctx.cancel_upcoming_room_meals
    create_session = ctx.create_session
    demo_mode = ctx.demo_mode
    invalidate = ctx.invalidate
    login_limit = ctx.login_limit
    remove_member = ctx.remove_member
    session_view = ctx.session_view
    store = ctx.store
    user_view = ctx.user_view

    @router.get("/session")
    def session(request: Request):
        try:
            auth = authenticate(request)
        except HTTPException as error:
            if error.status_code != 401:
                raise
            return {
                "user": None,
                "profile": None,
                "csrf_token": None,
                "demo_mode": demo_mode,
            }
        return session_view(auth["user_id"], auth["csrf_token"])

    @router.post("/auth/register", status_code=201)
    def register(body: Registration, request: Request, response: Response):
        login_limit(request, body.email)
        if not body.adult_confirmed or not body.terms_accepted:
            raise HTTPException(
                422, "Confirm adult eligibility and accept the pilot terms"
            )
        user_id, password_hash = new_id(), PASSWORD_HASHER.hash(body.password)
        try:
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO users(id,email,name,password_hash,profile,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        user_id,
                        body.email,
                        body.name,
                        password_hash,
                        encode(DEFAULT_PROFILE),
                        stamp(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An account already uses this email") from None
        return create_session(
            user_id, response, request, verified_password_hash=password_hash
        )

    @router.post("/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        login_limit(request, body.email)
        with store.transaction() as db:
            user = db.execute(
                "SELECT * FROM users WHERE email=?", (body.email,)
            ).fetchone()
        correct = PASSWORD_HASHER.verify(
            body.password, user["password_hash"] if user else DUMMY_PASSWORD_HASH
        )
        if not user or not correct:
            raise HTTPException(401, "Email or password is incorrect")
        return create_session(
            user["id"], response, request, verified_password_hash=user["password_hash"]
        )

    @router.post("/auth/logout")
    def logout(response: Response, auth=Auth):
        with store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (auth["token_hash"],))
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return {
            "user": None,
            "profile": None,
            "csrf_token": None,
            "demo_mode": demo_mode,
        }

    @router.get("/profile")
    def get_profile(auth=Auth):
        return session_view(auth["user_id"], auth["csrf_token"])["profile"]

    @router.patch("/profile")
    def update_profile(body: dict, auth=Auth):
        if not body or set(body) - set(Profile.model_fields) - {
            "expected_profile_revision"
        }:
            raise HTTPException(422, "Provide supported profile fields")
        with store.transaction() as db:
            user = db.execute(
                "SELECT * FROM users WHERE id=?", (auth["user_id"],)
            ).fetchone()
            expected = body.get("expected_profile_revision")
            if expected is not None and (
                type(expected) is not int or expected != user["profile_revision"]
            ):
                raise HTTPException(
                    409,
                    {
                        "code": "PROFILE_CHANGED",
                        "message": "Your profile changed in another tab. Review the current values.",
                        "current_profile": {
                            **decode(user["profile"]),
                            "profile_revision": user["profile_revision"],
                        },
                    },
                )
            data = decode(user["profile"])
            data.update(
                {k: v for k, v in body.items() if k != "expected_profile_revision"}
            )
            try:
                profile = Profile.model_validate(data).model_dump()
            except ValueError as error:
                raise HTTPException(
                    422,
                    "Profile values are inconsistent; check requirement states and sensitive-data consent",
                ) from error
            if profile != decode(user["profile"]):
                db.execute(
                    "UPDATE users SET profile=?,profile_revision=profile_revision+1 WHERE id=?",
                    (encode(profile), auth["user_id"]),
                )
                for row in db.execute(
                    "SELECT meal_id FROM participants WHERE user_id=?",
                    (auth["user_id"],),
                ).fetchall():
                    invalidate(db, row["meal_id"])
                    db.execute(
                        "UPDATE participants SET response_revision=response_revision+1,response=json_set(response,'$.requirements_confirmed',json('false'),'$.ready',json('false')) WHERE meal_id=? AND user_id=? AND response IS NOT NULL AND EXISTS(SELECT 1 FROM meals WHERE id=? AND status NOT IN ('selected','manual_selected','awaiting_feedback','closed','cancelled','expired'))",
                        (row["meal_id"], auth["user_id"], row["meal_id"]),
                    )
            if not profile["memory_enabled"]:
                db.execute(
                    "DELETE FROM learning_suggestion_log WHERE user_id=?",
                    (auth["user_id"],),
                )
                db.execute(
                    "DELETE FROM observations WHERE user_id=?", (auth["user_id"],)
                )
            audit(db, auth["user_id"], "profile_updated")
            profile["profile_revision"] = db.execute(
                "SELECT profile_revision FROM users WHERE id=?", (auth["user_id"],)
            ).fetchone()[0]
            return profile

    @router.get("/consent")
    def get_consent(auth=Auth):
        with store.transaction() as db:
            return ConsentManager.get_consent_status(db, auth["user_id"])

    @router.post("/consent")
    def record_consent(body: ConsentInput, auth=Auth):
        with store.transaction() as db:
            record = ConsentManager.record_consent(
                db,
                auth["user_id"],
                body.purpose,
                body.notice_version,
                body.decision,
                body.source_interface,
            )
            audit(db, auth["user_id"], f"consent_{body.purpose}_{body.decision}")
            return record

    @router.post("/push/subscriptions", status_code=201)
    def register_push_subscription(body: PushSubscriptionInput, auth=Auth):
        with store.transaction() as db:
            return PushSubscriptionManager.register(
                db,
                auth["user_id"],
                body.endpoint,
                body.p256dh,
                body.auth,
                body.user_agent,
            )

    @router.get("/push/subscriptions")
    def list_push_subscriptions(auth=Auth):
        with store.transaction() as db:
            return PushSubscriptionManager.list_user_subscriptions(db, auth["user_id"])

    @router.post("/push/unsubscribe")
    def unsubscribe_push(body: PushUnsubscribeInput, auth=Auth):
        with store.transaction() as db:
            success = PushSubscriptionManager.unsubscribe(
                db, auth["user_id"], body.endpoint
            )
            if not success:
                raise HTTPException(404, "Subscription not found")
            return {"unsubscribed": True}

    @router.get("/notifications")
    def notifications(auth=Auth):
        with store.transaction() as db:
            rows = db.execute(
                "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
                (auth["user_id"],),
            ).fetchall()
            return {
                "notifications": [
                    {
                        "id": r["id"],
                        "room_id": r["room_id"],
                        "meal_id": r["meal_id"],
                        "kind": r["kind"],
                        "message": r["message"],
                        "created_at": r["created_at"],
                        "read": bool(r["read"]),
                    }
                    for r in rows
                ]
            }

    @router.post("/notifications/{notification_id}/read")
    def read_notification(notification_id: str, auth=Auth):
        with store.transaction() as db:
            cursor = db.execute(
                "UPDATE notifications SET read=1 WHERE id=? AND user_id=?",
                (notification_id, auth["user_id"]),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Notification not found")
        return {"ok": True}

    @router.get("/export")
    def export(auth=Auth):
        with store.transaction() as db:
            user = db.execute(
                "SELECT * FROM users WHERE id=?", (auth["user_id"],)
            ).fetchone()
            return {
                "exported_at": stamp(),
                "user": user_view(user),
                "profile": decode(user["profile"]),
                "room_memberships": [
                    {"room_id": r["room_id"], "joined_at": r["joined_at"]}
                    for r in db.execute(
                        "SELECT * FROM members WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "meal_responses": [
                    {
                        "meal_id": r["meal_id"],
                        "attendance": r["attendance"],
                        "response": decode(r["response"]),
                    }
                    for r in db.execute(
                        "SELECT * FROM participants WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "feedback": [
                    {"meal_id": r["meal_id"], **decode(r["payload"])}
                    for r in db.execute(
                        "SELECT * FROM feedback WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "private_votes": [
                    {
                        "meal_id": r["meal_id"],
                        "option_id": r["option_id"],
                        "revision": r["revision"],
                        "choice": r["choice"],
                        "reason": r["reason"],
                    }
                    for r in db.execute(
                        "SELECT * FROM votes WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "delegations": [
                    {
                        "meal_id": r["meal_id"],
                        "revision": r["revision"],
                        "option_ids": decode(r["option_ids"]),
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM delegations WHERE user_id=?", (auth["user_id"],)
                    )
                ],
                "manual_acknowledgements": [
                    {
                        "meal_id": r["meal_id"],
                        "revision": r["revision"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM manual_acknowledgements WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "observations": learning_view(db, auth["user_id"])["observations"],
                "preference_proposals": learning_view(db, auth["user_id"])["proposals"],
                "data_error_reports": [
                    {
                        "id": r["id"],
                        "meal_id": r["meal_id"],
                        "outlet_id": r["outlet_id"],
                        "category": r["category"],
                        "description": r["description"],
                        "status": r["status"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM data_error_reports WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "meal_origins": [
                    {
                        "meal_id": r["meal_id"],
                        "origin_mode": r["origin_mode"],
                        "latitude": r["latitude"],
                        "longitude": r["longitude"],
                        "approximate_area": r["approximate_area"],
                        "route_consent": bool(r["route_consent"]),
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM meal_origins WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "exposure_events": [
                    {
                        "meal_id": r["meal_id"],
                        "meal_revision": r["meal_revision"],
                        "event_type": r["event_type"],
                        "outlet_id": r["outlet_id"],
                        "option_id": r["option_id"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM exposure_events WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "personal_recommendations": [
                    {
                        "meal_id": r["meal_id"],
                        "meal_revision": r["meal_revision"],
                        "outlet_id": r["outlet_id"],
                        "item_id": r["item_id"],
                        "rank": r["rank"],
                        "score": r["score"],
                        "status": r["status"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM personal_recommendations WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
                "consent_records": [
                    {
                        "id": r["id"],
                        "notice_version": r["notice_version"],
                        "purpose": r["purpose"],
                        "decision": r["decision"],
                        "source_interface": r["source_interface"],
                        "timestamp": r["timestamp"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT * FROM consent_records WHERE user_id=? ORDER BY created_at",
                        (auth["user_id"],),
                    )
                ],
                "push_subscriptions": [
                    {
                        "id": r["id"],
                        "endpoint": r["endpoint"],
                        "status": r["status"],
                        "created_at": r["created_at"],
                    }
                    for r in db.execute(
                        "SELECT id, endpoint, status, created_at FROM push_subscriptions WHERE user_id=?",
                        (auth["user_id"],),
                    )
                ],
            }

    @router.delete("/account")
    def delete_account(response: Response, auth=Auth):
        with store.transaction() as db:
            rooms = db.execute(
                "SELECT r.* FROM rooms r JOIN members m ON m.room_id=r.id WHERE m.user_id=?",
                (auth["user_id"],),
            ).fetchall()
            for room in rooms:
                if room["owner_id"] == auth["user_id"]:
                    db.execute(
                        "UPDATE rooms SET archived=1,invite_hash=NULL,invite_expires=NULL WHERE id=?",
                        (room["id"],),
                    )
                    cancel_upcoming_room_meals(db, room["id"])
                else:
                    remove_member(db, room, auth["user_id"], auth["user_id"])
            # Remove the deleted account ID from shared immutable decision summaries.
            for meal in db.execute(
                "SELECT id,decision,frozen_participants FROM meals WHERE decision IS NOT NULL OR frozen_participants IS NOT NULL"
            ).fetchall():
                decision = decode(meal["decision"])
                if decision is not None:
                    decision["participant_ids"] = [
                        uid
                        for uid in decision.get("participant_ids", [])
                        if uid != auth["user_id"]
                    ]
                    if decision.get("selected_by") == auth["user_id"]:
                        decision["selected_by"] = None
                frozen = [
                    uid
                    for uid in decode(meal["frozen_participants"], [])
                    if uid != auth["user_id"]
                ]
                db.execute(
                    "UPDATE meals SET decision=?,frozen_participants=? WHERE id=?",
                    (
                        encode(decision) if decision is not None else None,
                        encode(frozen),
                        meal["id"],
                    ),
                )
            db.execute("DELETE FROM users WHERE id=?", (auth["user_id"],))
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return {
            "ok": True,
            "message": "Account and private records deleted. Rooms you owned were archived.",
        }
