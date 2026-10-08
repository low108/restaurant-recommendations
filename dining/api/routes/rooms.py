"""Room routes: create, preview and join by invite, membership and ownership."""

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, HTTPException

from dining.api.common import (
    digest,
    new_id,
    stamp,
)
from dining.api.context import ApiContext
from dining.api.schemas import (
    Join,
    RoomCreate,
    Transfer,
)
from dining.core.constants import INVITE_SECONDS
from dining.core.store import decode


def register(router: APIRouter, ctx: ApiContext) -> None:
    """Add this module's routes to ``router``."""
    # Shared helpers, bound to local names so the route bodies read naturally.
    Auth = ctx.Auth
    audit = ctx.audit
    cancel_upcoming_room_meals = ctx.cancel_upcoming_room_meals
    refresh_lifecycle = ctx.refresh_lifecycle
    remove_member = ctx.remove_member
    room_member = ctx.room_member
    room_view = ctx.room_view
    store = ctx.store

    @router.get("/rooms")
    def rooms(auth=Auth):
        with store.transaction() as db:
            rows = db.execute(
                "SELECT r.* FROM rooms r JOIN members m ON m.room_id=r.id WHERE m.user_id=? ORDER BY r.created_at DESC",
                (auth["user_id"],),
            ).fetchall()
            return {"rooms": [room_view(db, r) for r in rows]}

    @router.post("/rooms", status_code=201)
    def create_room(body: RoomCreate, auth=Auth):
        room_id, token = new_id(), secrets.token_urlsafe(32)
        with store.transaction() as db:
            db.execute(
                "INSERT INTO rooms(id,name,owner_id,invite_hash,invite_expires,created_at) VALUES(?,?,?,?,?,?)",
                (
                    room_id,
                    body.name,
                    auth["user_id"],
                    digest(token),
                    time.time() + INVITE_SECONDS,
                    stamp(),
                ),
            )
            db.execute(
                "INSERT INTO members VALUES(?,?,?)", (room_id, auth["user_id"], stamp())
            )
            audit(db, auth["user_id"], "room_created", room_id)
            room = room_view(
                db, db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
            )
        return {"room": room, "invite_token": token}

    @router.get("/rooms/preview")
    def preview_room(token: str):
        with store.transaction() as db:
            room = db.execute(
                "SELECT * FROM rooms WHERE invite_hash=? AND invite_expires>? AND archived=0",
                (digest(token), time.time()),
            ).fetchone()
            if room is None:
                raise HTTPException(404, "This invitation expired or was revoked")
            member_count = db.execute(
                "SELECT COUNT(*) FROM members WHERE room_id=?", (room["id"],)
            ).fetchone()[0]
            owner = None
            if room["owner_id"]:
                owner_row = db.execute(
                    "SELECT name FROM users WHERE id=?", (room["owner_id"],)
                ).fetchone()
                if owner_row:
                    owner = owner_row["name"]
            return {
                "room_id": room["id"],
                "name": room["name"],
                "member_count": member_count,
                "created_at": room["created_at"],
                "owner_name": owner,
            }

    @router.post("/rooms/join")
    def join_room(body: Join, auth=Auth):
        with store.transaction() as db:
            room = db.execute(
                "SELECT * FROM rooms WHERE invite_hash=? AND invite_expires>? AND archived=0",
                (digest(body.token), time.time()),
            ).fetchone()
            if room is None:
                raise HTTPException(404, "This invitation expired or was revoked")
            existing = db.execute(
                "SELECT 1 FROM members WHERE room_id=? AND user_id=?",
                (room["id"], auth["user_id"]),
            ).fetchone()
            if not existing:
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM members WHERE room_id=?", (room["id"],)
                    ).fetchone()[0]
                    >= 20
                ):
                    raise HTTPException(
                        409, "This pilot supports up to 20 members per room"
                    )
                db.execute(
                    "INSERT INTO members VALUES(?,?,?)",
                    (room["id"], auth["user_id"], stamp()),
                )
                db.execute(
                    "UPDATE rooms SET membership_revision=membership_revision+1 WHERE id=?",
                    (room["id"],),
                )
                audit(db, auth["user_id"], "room_joined", room["id"])
            return room_view(db, room)

    @router.get("/rooms/{room_id}")
    def room_detail(room_id: str, auth=Auth):
        with store.transaction() as db:
            room = room_member(db, room_id, auth["user_id"])
            output = room_view(db, room)
            output["members"] = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "is_owner": r["id"] == room["owner_id"],
                }
                for r in db.execute(
                    "SELECT u.id,u.name FROM users u JOIN members m ON m.user_id=u.id WHERE m.room_id=? ORDER BY m.joined_at",
                    (room_id,),
                )
            ]
            for pending in db.execute(
                "SELECT m.* FROM meals m JOIN participants p ON p.meal_id=m.id WHERE m.room_id=? AND p.user_id=?",
                (room_id, auth["user_id"]),
            ).fetchall():
                refresh_lifecycle(db, pending)
            output["meals"] = [
                {
                    "id": m["id"],
                    "status": m["status"],
                    "revision": m["revision"],
                    "organizer_id": m["organizer_id"],
                    **{
                        k: decode(m["payload"])[k]
                        for k in ("kind", "meal_at", "location_label")
                    },
                }
                for m in db.execute(
                    "SELECT m.* FROM meals m JOIN participants p ON p.meal_id=m.id WHERE m.room_id=? AND p.user_id=? ORDER BY m.created_at DESC",
                    (room_id, auth["user_id"]),
                )
            ]
            return output

    @router.post("/rooms/{room_id}/invite")
    def rotate_invite(room_id: str, auth=Auth):
        token = secrets.token_urlsafe(32)
        with store.transaction() as db:
            room_member(db, room_id, auth["user_id"], owner=True, active=True)
            db.execute(
                "UPDATE rooms SET invite_hash=?,invite_expires=? WHERE id=?",
                (digest(token), time.time() + INVITE_SECONDS, room_id),
            )
            audit(db, auth["user_id"], "invite_rotated", room_id)
        return {"invite_token": token, "expires_in_days": 7}

    @router.post("/rooms/{room_id}/leave")
    def leave_room(room_id: str, auth=Auth):
        with store.transaction() as db:
            room = room_member(db, room_id, auth["user_id"])
            if room["owner_id"] == auth["user_id"] and not room["archived"]:
                raise HTTPException(
                    409, "Transfer ownership or archive the room before leaving"
                )
            remove_member(db, room, auth["user_id"], auth["user_id"])
        return {"ok": True}

    @router.delete("/rooms/{room_id}/members/{user_id}")
    def kick_member(room_id: str, user_id: str, auth=Auth):
        with store.transaction() as db:
            room = room_member(db, room_id, auth["user_id"], owner=True, active=True)
            if user_id == room["owner_id"]:
                raise HTTPException(409, "Transfer ownership before removing the owner")
            if not db.execute(
                "SELECT 1 FROM members WHERE room_id=? AND user_id=?",
                (room_id, user_id),
            ).fetchone():
                raise HTTPException(404, "Member not found")
            remove_member(db, room, user_id, auth["user_id"])
        return {"ok": True}

    @router.post("/rooms/{room_id}/transfer")
    def transfer_owner(room_id: str, body: Transfer, auth=Auth):
        with store.transaction() as db:
            room_member(db, room_id, auth["user_id"], owner=True, active=True)
            if not db.execute(
                "SELECT 1 FROM members WHERE room_id=? AND user_id=?",
                (room_id, body.user_id),
            ).fetchone():
                raise HTTPException(422, "Choose an existing member")
            db.execute(
                "UPDATE rooms SET owner_id=? WHERE id=?", (body.user_id, room_id)
            )
            audit(db, auth["user_id"], "ownership_transferred", room_id)
        return {"ok": True}

    @router.post("/rooms/{room_id}/archive")
    def archive_room(room_id: str, auth=Auth):
        with store.transaction() as db:
            room_member(db, room_id, auth["user_id"], owner=True)
            db.execute(
                "UPDATE rooms SET archived=1,invite_hash=NULL,invite_expires=NULL WHERE id=?",
                (room_id,),
            )
            cancel_upcoming_room_meals(db, room_id)
            audit(db, auth["user_id"], "room_archived", room_id)
        return {"ok": True}
