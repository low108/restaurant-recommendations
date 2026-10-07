"""Durable, idempotent in-app reminders. No external notification sender is enabled."""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from dining.lifecycle import post_meal_transition
from dining.store import decode


class ReminderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reminders_enabled: bool


class RoomSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    muted: bool


class NotificationService:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            for statement in (
                "CREATE TABLE IF NOT EXISTS notification_preferences (user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,reminders_enabled INTEGER NOT NULL DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS room_notification_preferences (room_id TEXT REFERENCES rooms(id) ON DELETE CASCADE,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,muted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(room_id,user_id))",
                "CREATE TABLE IF NOT EXISTS notification_jobs (id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,room_id TEXT REFERENCES rooms(id) ON DELETE CASCADE,meal_id TEXT REFERENCES meals(id) ON DELETE CASCADE,kind TEXT NOT NULL,due_at TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',delivered_at TEXT)",
            ):
                db.execute(statement)

    def router(self, authenticate, room_member):
        router = APIRouter(prefix="/api")
        auth_dependency = Depends(authenticate)

        @router.get("/notification-settings")
        def settings(auth=auth_dependency):
            with self.store.transaction() as db:
                row = db.execute(
                    "SELECT reminders_enabled FROM notification_preferences WHERE user_id=?",
                    (auth["user_id"],),
                ).fetchone()
            return {
                "reminders_enabled": bool(row and row[0]),
                "push_available": False,
                "delivery": "in_app",
            }

        @router.patch("/notification-settings")
        def update_settings(body: ReminderSettings, auth=auth_dependency):
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO notification_preferences VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET reminders_enabled=excluded.reminders_enabled",
                    (auth["user_id"], body.reminders_enabled),
                )
            return settings(auth)

        @router.get("/rooms/{room_id}/notification-settings")
        def room_settings(room_id: str, auth=auth_dependency):
            with self.store.transaction() as db:
                room_member(db, room_id, auth["user_id"])
                row = db.execute(
                    "SELECT muted FROM room_notification_preferences WHERE room_id=? AND user_id=?",
                    (room_id, auth["user_id"]),
                ).fetchone()
            return {"muted": bool(row and row[0])}

        @router.patch("/rooms/{room_id}/notification-settings")
        def update_room_settings(
            room_id: str, body: RoomSettings, auth=auth_dependency
        ):
            with self.store.transaction() as db:
                room_member(db, room_id, auth["user_id"])
                db.execute(
                    "INSERT INTO room_notification_preferences VALUES(?,?,?) ON CONFLICT(room_id,user_id) DO UPDATE SET muted=excluded.muted",
                    (room_id, auth["user_id"], body.muted),
                )
            return {"muted": body.muted}

        return router

    def tick(self, *, at=None):
        """Create and deliver a due inbox event in one transaction, retryable after a crash."""
        at = at or datetime.now(timezone.utc)
        delivered = 0
        with self.store.transaction() as db:
            rows = db.execute("""SELECT m.*,p.user_id,p.attendance,p.response,
                COALESCE(np.reminders_enabled,0) AS reminders_enabled,
                COALESCE(rp.muted,0) AS muted
                FROM meals m JOIN participants p ON p.meal_id=m.id
                JOIN members mb ON mb.room_id=m.room_id AND mb.user_id=p.user_id
                JOIN rooms r ON r.id=m.room_id AND r.archived=0
                LEFT JOIN notification_preferences np ON np.user_id=p.user_id
                LEFT JOIN room_notification_preferences rp ON rp.room_id=m.room_id AND rp.user_id=p.user_id
                WHERE m.status NOT IN ('cancelled','expired','closed')""").fetchall()
            current_jobs = set()
            for row in rows:
                payload = decode(row["payload"])
                response = decode(row["response"], {})
                status = row["status"]
                transition = post_meal_transition(status, payload, at)
                if transition is not None:
                    next_status, reason = transition
                    changed = db.execute(
                        "UPDATE meals SET status=?,lifecycle_reason=? WHERE id=? AND status=?",
                        (next_status, reason, row["id"], status),
                    ).rowcount
                    if changed:
                        db.execute(
                            "INSERT INTO audit_events VALUES(?,?,?,?,?,?)",
                            (
                                str(uuid4()),
                                None,
                                row["room_id"],
                                row["id"],
                                "meal_" + next_status,
                                at.isoformat(),
                            ),
                        )
                    status = next_status
                if status == "closed":
                    continue
                if status in {
                    "selected",
                    "manual_selected",
                    "awaiting_feedback",
                }:
                    decision = decode(row["decision"], {})
                    if row["user_id"] not in decision.get("participant_ids", []):
                        continue
                    if db.execute(
                        "SELECT 1 FROM feedback WHERE meal_id=? AND user_id=?",
                        (row["id"], row["user_id"]),
                    ).fetchone():
                        continue
                    due = datetime.fromisoformat(payload["meal_at"]) + timedelta(
                        minutes=payload.get("duration_minutes", 60) + 60
                    )
                    expires = due + timedelta(days=7)
                    kind = "feedback_available"
                    message = "Did you eat together? Your private feedback is ready."
                elif (
                    row["status"]
                    in {"collecting", "needs_input", "reconfirmation_required"}
                    and row["reminders_enabled"]
                    and not row["muted"]
                    and row["attendance"] not in {"decline", "withdrawn", "excluded"}
                    and not response.get("ready")
                ):
                    expires = datetime.fromisoformat(payload["answer_by"])
                    due = expires - timedelta(minutes=10)
                    kind = "checkin_reminder"
                    message = "A meal plan is waiting for your answer."
                else:
                    continue
                job_id = uuid5(
                    NAMESPACE_URL,
                    f"dining:{row['id']}:{row['user_id']}:{kind}:{due.isoformat()}",
                ).hex
                current_jobs.add(job_id)
                db.execute(
                    "INSERT OR IGNORE INTO notification_jobs(id,user_id,room_id,meal_id,kind,due_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        job_id,
                        row["user_id"],
                        row["room_id"],
                        row["id"],
                        kind,
                        due.isoformat(),
                        expires.isoformat(),
                    ),
                )
                if at < expires:
                    # A readiness/profile or mute change can make the same pending
                    # reminder relevant again. Delivered and expired jobs stay final.
                    db.execute(
                        "UPDATE notification_jobs SET status='queued' WHERE id=? AND status='suppressed'",
                        (job_id,),
                    )
                job = db.execute(
                    "SELECT status FROM notification_jobs WHERE id=?", (job_id,)
                ).fetchone()
                if job[0] != "queued":
                    continue
                if at >= expires:
                    db.execute(
                        "UPDATE notification_jobs SET status='expired' WHERE id=?",
                        (job_id,),
                    )
                elif at >= due:
                    db.execute(
                        "INSERT OR IGNORE INTO notifications VALUES(?,?,?,?,?,?,?,0)",
                        (
                            job_id,
                            row["user_id"],
                            row["room_id"],
                            row["id"],
                            kind,
                            message,
                            at.isoformat(),
                        ),
                    )
                    db.execute(
                        "UPDATE notification_jobs SET status='delivered',delivered_at=? WHERE id=?",
                        (at.isoformat(), job_id),
                    )
                    delivered += 1
            for row in db.execute(
                "SELECT id FROM notification_jobs WHERE status='queued'"
            ).fetchall():
                if row["id"] not in current_jobs:
                    db.execute(
                        "UPDATE notification_jobs SET status='suppressed' WHERE id=?",
                        (row["id"],),
                    )
        return {"delivered": delivered}
