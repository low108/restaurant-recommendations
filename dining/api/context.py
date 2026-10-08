"""Shared state and helpers behind every API route.

:class:`ApiContext` replaces the old closure variables of ``build_router``: it owns the
store, the recommender and the request helpers (authentication, membership checks, meal
lifecycle, invalidation, and the participant's meal view). Route modules receive one
context and bind the helpers they need.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response

from dining.api.common import COOKIE, SESSION_SECONDS, digest, new_id, now, stamp
from dining.core.constants import (
    DECIDED_STATUSES,
    DEFAULT_MEAL_MINUTES,
    FINALIZED_STATUSES,
    POST_DECISION_STATUSES,
    TERMINAL_STATUSES,
)
from dining.core.store import DiningStore, decode
from dining.meals.generation import (
    job_view,
    supersede_jobs,
)
from dining.meals.lifecycle import planned_finish, post_meal_transition
from dining.recommendation.personal import (
    get_personal_recommendations,
)


class ApiContext:
    """Dependencies and shared helpers for one API router."""

    def __init__(
        self,
        store: DiningStore,
        recommend: Callable[[dict], dict],
        demo_mode: bool = False,
        account_access: Callable[[str], None] | None = None,
        async_generation: bool = False,
        generation_options: dict | None = None,
    ):
        self.store = store
        self.recommend = recommend
        self.demo_mode = demo_mode
        self.account_access = account_access
        self.async_generation = async_generation
        self.generation_options = generation_options
        # Route parameter dependency: ``auth=ctx.Auth`` yields the session row.
        self.Auth = Depends(self.authenticate)
        # Set by :class:`~dining.api.generation.GenerationPipeline` when it is created.
        self.generation = None
        self.generation_worker = None

    # ---------------------------------------------------------- sessions and sign-in
    def origin_check(self, request: Request):
        """Refuse cross-site requests and an Origin header that is not this application."""
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site requests are not permitted")
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.netloc != request.url.netloc
                or parsed.scheme != request.url.scheme
            ):
                raise HTTPException(403, "Origin does not match this application")

    def authenticate(self, request: Request):
        """FastAPI dependency: the signed-in session row, with CSRF and origin checks on writes."""
        token = request.cookies.get(COOKIE)
        if not token:
            raise HTTPException(401, "Sign in to continue")
        with self.store.transaction() as db:
            session = db.execute(
                "SELECT s.*,u.email,u.name FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
                (digest(token), time.time()),
            ).fetchone()
        if session is None:
            raise HTTPException(401, "Your session expired. Sign in again")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.origin_check(request)
            if not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), session["csrf_token"]
            ):
                raise HTTPException(
                    403, "Refresh this page and try again (CSRF token required)"
                )
        if (
            self.account_access
            and request.url.path
            not in {"/api/session", "/api/profile", "/api/export", "/api/account"}
            and not request.url.path.startswith("/api/auth/")
        ):
            self.account_access(session["user_id"])
        return dict(session)

    def user_view(self, row):
        """Public account fields."""
        return {"id": row["id"], "email": row["email"], "name": row["name"]}

    def session_view(self, user_id, csrf):
        """What the browser needs after sign-in: user, profile, CSRF token and mode."""
        with self.store.transaction() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {
            "user": self.user_view(user),
            "profile": {
                **decode(user["profile"]),
                "profile_revision": user["profile_revision"],
            },
            "csrf_token": csrf,
            "demo_mode": self.demo_mode,
        }

    def create_session(
        self, user_id, response: Response, request: Request, verified_password_hash=None
    ):
        """Start a session cookie for a user whose password hash is unchanged."""
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT password_hash FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if current is None or (
                verified_password_hash is not None
                and current["password_hash"] != verified_password_hash
            ):
                raise HTTPException(401, "Credentials changed. Sign in again")
            db.execute("DELETE FROM sessions WHERE expires_at<?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (digest(token), user_id, csrf, time.time() + SESSION_SECONDS),
            )
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return self.session_view(user_id, csrf)

    def login_limit(self, request: Request, email: str):
        """Rate-limit sign-in per account+IP and per IP over a 15-minute window."""
        # Persist both account+IP and IP-wide buckets. No trusted forwarded IP in this local server.
        self.origin_check(request)
        if (
            not request.headers.get("content-type", "")
            .lower()
            .startswith("application/json")
        ):
            raise HTTPException(415, "Send JSON")
        client = request.client.host if request.client else "unknown"
        account_max = 500 if self.demo_mode else 10
        ip_max = 2000 if self.demo_mode else 50
        buckets = [
            (digest("account:" + client + ":" + email), account_max),
            (digest("ip:" + client), ip_max),
        ]
        blocked = False
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM login_attempts WHERE attempted_at<?", (time.time() - 900,)
            )
            for bucket, maximum in buckets:
                count = db.execute(
                    "SELECT COUNT(*) FROM login_attempts WHERE bucket=?", (bucket,)
                ).fetchone()[0]
                blocked |= count >= maximum
            if not blocked:
                db.executemany(
                    "INSERT INTO login_attempts VALUES(?,?)",
                    [(b, time.time()) for b, _ in buckets],
                )
        if blocked:
            raise HTTPException(
                429, "Too many sign-in attempts. Try again in 15 minutes"
            )

    # ------------------------------------------------- membership and meal lifecycle
    def room_member(self, db, room_id, user_id, owner=False, active=False):
        """The room row if the user is a member (optionally owner / room still active)."""
        row = db.execute(
            "SELECT r.* FROM rooms r JOIN members m ON m.room_id=r.id WHERE r.id=? AND m.user_id=?",
            (room_id, user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Room not found")
        if owner and row["owner_id"] != user_id:
            raise HTTPException(403, "Only the room owner can do that")
        if active and row["archived"]:
            raise HTTPException(409, "This room is archived")
        return row

    def refresh_lifecycle(self, db, meal):
        """Advance a meal past deadlines (overdue, expired, feedback) before reading it."""
        if meal["status"] in TERMINAL_STATUSES:
            return meal
        payload = decode(meal["payload"])
        end = planned_finish(payload)
        current = now()
        if meal["status"] in POST_DECISION_STATUSES:
            transition = post_meal_transition(meal["status"], payload, current)
            if transition is None:
                return meal
            status, reason = transition
        else:
            status = (
                "expired"
                if current >= end
                else (
                    "decision_overdue"
                    if current >= datetime.fromisoformat(payload["decision_by"])
                    else meal["status"]
                )
            )
            reason = (
                "Planned meal ended without a confirmed decision"
                if status == "expired"
                else "Decision deadline passed without a confirmed decision"
            )
        if status != meal["status"]:
            if status in {"expired", "decision_overdue"}:
                supersede_jobs(db, meal["id"])
            db.execute(
                "UPDATE meals SET status=?,lifecycle_reason=? WHERE id=?",
                (status, reason, meal["id"]),
            )
            if status == "expired":
                db.execute(
                    "UPDATE meals SET result=NULL,result_revision=NULL,manual_plan=NULL,frozen_participants=NULL WHERE id=?",
                    (meal["id"],),
                )
                db.execute("DELETE FROM votes WHERE meal_id=?", (meal["id"],))
                db.execute("DELETE FROM delegations WHERE meal_id=?", (meal["id"],))
                db.execute(
                    "DELETE FROM manual_acknowledgements WHERE meal_id=?", (meal["id"],)
                )
            self.audit(db, None, "meal_" + status, meal["room_id"], meal["id"])
            meal = db.execute(
                "SELECT * FROM meals WHERE id=?", (meal["id"],)
            ).fetchone()
        return meal

    def meal_member(
        self, db, meal_id, user_id, organizer=False, mutable=False, allow_selected=False
    ):
        """The meal row if the user is invited, after lifecycle refresh and permission checks."""
        meal = db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
        if meal is None:
            raise HTTPException(404, "Meal not found")
        self.room_member(db, meal["room_id"], user_id, active=mutable)
        participant = db.execute(
            "SELECT * FROM participants WHERE meal_id=? AND user_id=?",
            (meal_id, user_id),
        ).fetchone()
        if participant is None:
            raise HTTPException(404, "Meal not found")
        if organizer and meal["organizer_id"] != user_id:
            raise HTTPException(403, "Only the meal organizer can do that")
        meal = self.refresh_lifecycle(db, meal)
        if mutable and meal["status"] in FINALIZED_STATUSES:
            revisable = (
                allow_selected
                and meal["status"] in DECIDED_STATUSES
                and datetime.fromisoformat(decode(meal["payload"])["meal_at"]) > now()
            )
            if not revisable:
                raise HTTPException(409, "This meal is already finalized")
        return meal

    def room_view(self, db, room):
        """Room summary with member count."""
        return {
            "id": room["id"],
            "name": room["name"],
            "owner_id": room["owner_id"],
            "archived": bool(room["archived"]),
            "member_count": db.execute(
                "SELECT COUNT(*) FROM members WHERE room_id=?", (room["id"],)
            ).fetchone()[0],
        }

    # ----------------------------------------- audit trail, notices and invalidation
    def audit(self, db, actor, kind, room_id=None, meal_id=None):
        """Append an audit event."""
        db.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?,?,?)",
            (new_id(), actor, room_id, meal_id, kind, stamp()),
        )

    def notify(self, db, users, kind, message, room_id=None, meal_id=None):
        """Insert one inbox notification per distinct user."""
        db.executemany(
            "INSERT INTO notifications VALUES(?,?,?,?,?,?,?,0)",
            [
                (new_id(), uid, room_id, meal_id, kind, message, stamp())
                for uid in set(users)
            ],
        )

    def invalidate(self, db, meal_id):
        """Start a new meal revision: drop the shortlist, votes and decisions.

        Returns False when the meal is finished and must not change.
        """
        meal = db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
        if meal is None or meal["status"] in FINALIZED_STATUSES - DECIDED_STATUSES:
            return False
        if (
            meal["status"] in DECIDED_STATUSES
            and datetime.fromisoformat(decode(meal["payload"])["meal_at"]) <= now()
        ):
            return False  # Preserve completed shared history; future outings need new consent.
        supersede_jobs(db, meal_id)
        reconfirm = meal["status"] in DECIDED_STATUSES or bool(
            meal["reconfirmation_required"]
        )
        db.execute(
            "UPDATE meals SET revision=revision+1,status=?,reconfirmation_required=?,result=NULL,result_revision=NULL,frozen_participants=NULL,decision=NULL,manual_plan=NULL,lifecycle_reason=NULL WHERE id=?",
            (
                "reconfirmation_required" if reconfirm else "collecting",
                int(reconfirm),
                meal_id,
            ),
        )
        db.execute("DELETE FROM votes WHERE meal_id=?", (meal_id,))
        db.execute("DELETE FROM delegations WHERE meal_id=?", (meal_id,))
        db.execute("DELETE FROM manual_acknowledgements WHERE meal_id=?", (meal_id,))
        if meal["status"] in DECIDED_STATUSES:
            self.notify(
                db,
                [
                    p["user_id"]
                    for p in db.execute(
                        "SELECT user_id FROM participants WHERE meal_id=?", (meal_id,)
                    )
                ],
                "reconfirmation_required",
                "The meal changed. A new checked decision needs everyone's confirmation.",
                meal["room_id"],
                meal_id,
            )
        return True

    def invalidate_member_meals(self, db, room_id, user_id):
        """Invalidate every open meal in a room that includes this user."""
        meals = db.execute(
            "SELECT m.id FROM meals m JOIN participants p ON p.meal_id=m.id WHERE m.room_id=? AND p.user_id=? AND m.status!='cancelled'",
            (room_id, user_id),
        ).fetchall()
        for row in meals:
            self.invalidate(db, row["id"])

    def cancel_upcoming_room_meals(self, db, room_id):
        """Cancel every room meal that can still change."""
        for meal in db.execute(
            "SELECT id FROM meals WHERE room_id=? AND status!='cancelled'", (room_id,)
        ).fetchall():
            if self.invalidate(db, meal["id"]):
                db.execute(
                    "UPDATE meals SET status='cancelled' WHERE id=?", (meal["id"],)
                )

    def remove_member(self, db, room, user_id, actor):
        """Remove a member from a room and repair meals they organised or joined."""
        self.invalidate_member_meals(db, room["id"], user_id)
        db.execute(
            "DELETE FROM participants WHERE user_id=? AND meal_id IN (SELECT id FROM meals WHERE room_id=? AND status NOT IN ('selected','manual_selected','awaiting_feedback','closed','cancelled','expired'))",
            (user_id, room["id"]),
        )
        for affected in db.execute(
            "SELECT id FROM meals WHERE room_id=? AND organizer_id=? AND status NOT IN ('selected','manual_selected','awaiting_feedback','closed','cancelled','expired')",
            (room["id"], user_id),
        ).fetchall():
            remaining = [
                r["user_id"]
                for r in db.execute(
                    "SELECT p.user_id FROM participants p JOIN members m ON m.user_id=p.user_id AND m.room_id=? WHERE p.meal_id=? ORDER BY p.user_id",
                    (room["id"], affected["id"]),
                )
            ]
            replacement = (
                room["owner_id"]
                if room["owner_id"] in remaining
                else (remaining[0] if remaining else None)
            )
            db.execute(
                "UPDATE meals SET organizer_id=? WHERE id=?",
                (replacement, affected["id"]),
            )
            if len(remaining) < 2:
                db.execute(
                    "UPDATE meals SET status='cancelled' WHERE id=?", (affected["id"],)
                )
        db.execute(
            "DELETE FROM members WHERE room_id=? AND user_id=?", (room["id"], user_id)
        )
        db.execute(
            "DELETE FROM notifications WHERE room_id=? AND user_id=?",
            (room["id"], user_id),
        )
        db.execute(
            "UPDATE rooms SET membership_revision=membership_revision+1 WHERE id=?",
            (room["id"],),
        )
        self.audit(db, actor, "member_removed", room["id"])

    # ------------------------------------------------------ views and request checks
    def approved_users(self, db, meal, option_id):
        """Participants who approved an option, directly or by delegation."""
        included = set(decode(meal["frozen_participants"], []))
        votes = {
            r["user_id"]: r["choice"]
            for r in db.execute(
                "SELECT user_id,choice FROM votes WHERE meal_id=? AND revision=? AND option_id=?",
                (meal["id"], meal["revision"], option_id),
            )
        }
        delegated = {
            r["user_id"]
            for r in db.execute(
                "SELECT user_id,option_ids FROM delegations WHERE meal_id=? AND revision=?",
                (meal["id"], meal["revision"]),
            )
            if option_id in decode(r["option_ids"], [])
        }
        # A participant's explicit negative choice always overrides delegation.
        return {
            uid
            for uid in included
            if votes.get(uid) == "works" or (uid in delegated and uid not in votes)
        }

    def meal_view(self, db, meal, user_id):
        """Everything one participant may see about a meal (their own private data only)."""
        meal = self.refresh_lifecycle(db, meal)
        result = dict(decode(meal["payload"]))
        result.update(
            {
                "id": meal["id"],
                "room_id": meal["room_id"],
                "organizer_id": meal["organizer_id"],
                "revision": meal["revision"],
                "status": meal["status"],
                "created_at": meal["created_at"],
                "original_answer_by": meal["original_answer_by"],
                "original_decision_by": meal["original_decision_by"],
                "original_invited_count": meal["original_invited_count"],
                "reconfirmation_required": bool(meal["reconfirmation_required"]),
                "lifecycle_reason": meal["lifecycle_reason"],
            }
        )
        rows = db.execute(
            "SELECT p.*,u.name FROM participants p JOIN users u ON u.id=p.user_id WHERE p.meal_id=? ORDER BY u.name",
            (meal["id"],),
        ).fetchall()
        result["participants"] = [
            {
                "id": p["user_id"],
                "name": p["name"],
                "attendance": p["attendance"],
                "ready": bool(decode(p["response"], {}).get("ready")),
                "responded": p["response"] is not None,
            }
            for p in rows
        ]
        own = next((p for p in rows if p["user_id"] == user_id), None)
        result["my_response"] = decode(own["response"]) if own else None
        result["my_response_revision"] = own["response_revision"] if own else 0
        origin_row = db.execute(
            "SELECT * FROM meal_origins WHERE meal_id=? AND user_id=?",
            (meal["id"], user_id),
        ).fetchone()
        result["my_origin"] = (
            {
                "origin_mode": origin_row["origin_mode"],
                "latitude": origin_row["latitude"],
                "longitude": origin_row["longitude"],
                "approximate_area": origin_row["approximate_area"],
                "route_consent": bool(origin_row["route_consent"]),
                "updated_at": origin_row["updated_at"],
            }
            if origin_row
            else None
        )
        result["my_votes"] = {
            r["option_id"]: {"choice": r["choice"], "reason": r["reason"]}
            for r in db.execute(
                "SELECT option_id,choice,reason FROM votes WHERE meal_id=? AND user_id=? AND revision=?",
                (meal["id"], user_id, meal["revision"]),
            )
        }
        delegation = db.execute(
            "SELECT * FROM delegations WHERE meal_id=? AND user_id=? AND revision=?",
            (meal["id"], user_id, meal["revision"]),
        ).fetchone()
        result["my_delegation"] = {
            "enabled": delegation is not None,
            "revision": meal["revision"],
            "option_ids": decode(delegation["option_ids"]) if delegation else [],
        }
        result["my_vote"] = {
            r["option_id"]: bool(r["approve"])
            for r in db.execute(
                "SELECT option_id,approve FROM votes WHERE meal_id=? AND user_id=? AND revision=?",
                (meal["id"], user_id, meal["revision"]),
            )
        }
        result["generation_worker_status"] = self.generation_worker.health()
        result["generation_history"] = [
            job_view(db, job)
            for job in db.execute(
                "SELECT * FROM generation_jobs WHERE meal_id=? ORDER BY rowid DESC LIMIT 10",
                (meal["id"],),
            )
        ]
        result["generation_job"] = next(
            (
                job
                for job in result["generation_history"]
                if job["context_revision"] == meal["revision"]
            ),
            None,
        )
        decoded_result = decode(meal["result"])
        if decoded_result and "_private_routes" in decoded_result:
            result["my_route_estimates"] = decoded_result.pop(
                "_private_routes", {}
            ).get(user_id, {})
        else:
            result["my_route_estimates"] = {}
        result["result"] = decoded_result
        active_cat = getattr(self.recommend, "catalog", None) or getattr(
            getattr(self.recommend, "recommender", None), "catalog", None
        )
        result["my_personal_recommendations"] = get_personal_recommendations(
            db, meal["id"], user_id, meal["revision"], catalog=active_cat
        )
        result["decision"] = decode(meal["decision"])
        result["manual_plan"] = decode(meal["manual_plan"])
        acknowledged = {
            r["user_id"]
            for r in db.execute(
                "SELECT user_id FROM manual_acknowledgements WHERE meal_id=? AND revision=?",
                (meal["id"], meal["revision"]),
            )
        }
        result["my_manual_ack"] = user_id in acknowledged
        result["manual_acceptance"] = {
            "acknowledged_count": len(acknowledged),
            "required_count": len(decode(meal["frozen_participants"], [])),
        }
        result["frozen_participant_ids"] = decode(meal["frozen_participants"], [])
        result["acceptance"] = {}
        for option in (result["result"] or {}).get("options", []):
            option_id = str(option.get("id", option.get("option_id", "")))
            approved = len(self.approved_users(db, meal, option_id))
            result["acceptance"][option_id] = {
                "approved_count": approved,
                "required_count": len(result["frozen_participant_ids"]),
            }
        feedback = db.execute(
            "SELECT payload FROM feedback WHERE meal_id=? AND user_id=?",
            (meal["id"], user_id),
        ).fetchone()
        result["my_feedback"] = decode(feedback["payload"]) if feedback else None

        # Schedule conflict detection (PRD REQ-03 / M04)
        conflicts = []
        participant_ids = [p["id"] for p in result["participants"]]
        if (
            participant_ids
            and result["status"] not in TERMINAL_STATUSES
            and "meal_at" in result
        ):
            meal_start = datetime.fromisoformat(result["meal_at"])
            meal_end = meal_start + timedelta(
                minutes=result.get("duration_minutes", DEFAULT_MEAL_MINUTES)
            )
            placeholders = ",".join("?" for _ in participant_ids)
            overlapping_rows = db.execute(
                f"""SELECT DISTINCT p.user_id FROM participants p
                    JOIN meals other ON other.id=p.meal_id
                    WHERE p.user_id IN ({placeholders})
                    AND other.id!=?
                    AND other.status NOT IN ('closed','cancelled','expired')
                """,
                (*participant_ids, meal["id"]),
            ).fetchall()
            for row in overlapping_rows:
                user_other_meals = db.execute(
                    "SELECT payload FROM meals m JOIN participants p ON p.meal_id=m.id WHERE p.user_id=? AND m.id!=? AND m.status NOT IN ('closed','cancelled','expired')",
                    (row["user_id"], meal["id"]),
                ).fetchall()
                for om in user_other_meals:
                    om_payload = decode(om["payload"], {})
                    if "meal_at" in om_payload:
                        om_start = datetime.fromisoformat(om_payload["meal_at"])
                        om_end = om_start + timedelta(
                            minutes=om_payload.get(
                                "duration_minutes", DEFAULT_MEAL_MINUTES
                            )
                        )
                        if max(meal_start, om_start) < min(meal_end, om_end):
                            conflicts.append(row["user_id"])
                            break
        conflict_count = len(set(conflicts))
        result["schedule_conflicts"] = (
            [
                {
                    "message": f"{conflict_count} invited participant{' has' if conflict_count == 1 else 's have'} an overlapping meal around this time"
                }
            ]
            if conflict_count > 0
            else []
        )
        return result

    def revision_check(self, meal, expected):
        """Reject a write made against an older meal revision."""
        if meal["revision"] != expected:
            raise HTTPException(
                409, "This meal changed. Refresh and review the new version"
            )

    def option_check(self, meal, option_id):
        """The shortlisted option a participant may vote for or select, or an HTTP error."""
        if (
            meal["result_revision"] != meal["revision"]
            or meal["status"] != "shortlisted"
        ):
            raise HTTPException(409, "Generate a current, eligible shortlist first")
        if datetime.fromisoformat(decode(meal["payload"])["decision_by"]) <= now():
            raise HTTPException(
                409,
                "The decision deadline passed. Update the meal times and regenerate",
            )
        options = decode(meal["result"], {}).get("options", [])
        option = next(
            (
                o
                for o in options
                if str(o.get("id", o.get("option_id", ""))) == option_id
            ),
            None,
        )
        if not option:
            raise HTTPException(422, "Choose an option in the current shortlist")
        if option.get("eligible") is False or option.get("status") in {
            "needs_verification",
            "ineligible",
        }:
            raise HTTPException(409, "This option has unresolved requirements")
        return option
