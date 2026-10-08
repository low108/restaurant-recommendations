"""Learning routes: review, delete or clear what the app learned from past meals."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException

from dining.api.common import (
    new_id,
    now,
    stamp,
)
from dining.api.context import ApiContext
from dining.api.schemas import (
    PreferenceReview,
)
from dining.core.store import decode


# ------------------------------------------------- learning records (shared with other routes)
def remove_observation(db, user_id, observation_id):
    """Delete one of the user's observations and any proposal built on it. False if not theirs."""
    own = db.execute(
        "SELECT id FROM observations WHERE id=? AND user_id=?",
        (observation_id, user_id),
    ).fetchone()
    if own is None:
        return False
    # Any source correction/deletion removes the dependent inference, even after acceptance.
    db.execute(
        "DELETE FROM preference_proposals WHERE user_id=? AND id IN (SELECT proposal_id FROM proposal_sources WHERE observation_id=?)",
        (user_id, observation_id),
    )
    db.execute(
        "DELETE FROM observations WHERE id=? AND user_id=?",
        (observation_id, user_id),
    )
    return True


def propose_repeat_pattern(db, user_id, outlet_id):
    """Propose a reviewable "repeat / avoid this outlet" preference.

    Needs three answers from three different meals that all agree, no open proposal for
    the outlet, no suggestion in the last 30 days and no matching rejection in 90 days.
    """
    records = db.execute(
        "SELECT * FROM observations WHERE user_id=? AND json_extract(payload,'$.outlet_id')=? AND json_extract(payload,'$.would_repeat') IS NOT NULL ORDER BY created_at DESC LIMIT 3",
        (user_id, outlet_id),
    ).fetchall()
    if len(records) < 3 or len({r["meal_id"] for r in records}) < 3:
        return
    signals = {decode(r["payload"])["would_repeat"] for r in records}
    if len(signals) != 1:
        return
    repeat = int(signals.pop())
    # Confirmed preferences remain authoritative; no automatic contradictory promotion.
    if db.execute(
        "SELECT 1 FROM preference_proposals WHERE user_id=? AND outlet_id=? AND status IN ('pending','accepted')",
        (user_id, outlet_id),
    ).fetchone():
        return
    if db.execute(
        "SELECT 1 FROM learning_suggestion_log WHERE user_id=? AND created_at>=?",
        (user_id, (now() - timedelta(days=30)).isoformat()),
    ).fetchone():
        return
    if db.execute(
        "SELECT 1 FROM learning_suggestion_log WHERE user_id=? AND outlet_id=? AND would_repeat=? AND decision='reject' AND reviewed_at>=?",
        (user_id, outlet_id, repeat, (now() - timedelta(days=90)).isoformat()),
    ).fetchone():
        return
    proposal_id = new_id()
    db.execute(
        "INSERT INTO preference_proposals(id,user_id,observation_id,outlet_id,would_repeat,created_at) VALUES(?,?,?,?,?,?)",
        (proposal_id, user_id, records[0]["id"], outlet_id, repeat, stamp()),
    )
    db.executemany(
        "INSERT INTO proposal_sources VALUES(?,?)",
        [(proposal_id, r["id"]) for r in records],
    )
    db.execute(
        "INSERT INTO learning_suggestion_log(id,user_id,outlet_id,would_repeat,created_at) VALUES(?,?,?,?,?)",
        (proposal_id, user_id, outlet_id, repeat, stamp()),
    )


def learning_view(db, user_id):
    """Everything the app learned about the user: observations, proposals and the opt-in flag."""
    user = db.execute("SELECT profile FROM users WHERE id=?", (user_id,)).fetchone()
    observations = [
        {
            "id": row["id"],
            "meal_id": row["meal_id"],
            "created_at": row["created_at"],
            **decode(row["payload"]),
        }
        for row in db.execute(
            "SELECT * FROM observations WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )
    ]
    proposals = []
    for raw_row in db.execute(
        "SELECT * FROM preference_proposals WHERE user_id=? ORDER BY created_at DESC",
        (user_id,),
    ):
        r = dict(raw_row)
        proposals.append(
            {
                "id": r["id"],
                "proposal_type": r.get("proposal_type") or "venue",
                "attribute": r.get("attribute"),
                "proposed_value": r.get("proposed_value"),
                "description": r.get("description"),
                "observation_id": r["observation_id"],
                "source_observation_ids": [
                    source["observation_id"]
                    for source in db.execute(
                        "SELECT observation_id FROM proposal_sources WHERE proposal_id=?",
                        (r["id"],),
                    )
                ],
                "outlet_id": r["outlet_id"],
                "would_repeat": bool(r["would_repeat"]),
                "status": r["status"],
                "created_at": r["created_at"],
                "reviewed_at": r["reviewed_at"],
            }
        )
    return {
        "enabled": bool(decode(user["profile"]).get("memory_enabled")),
        "observations": observations,
        "proposals": proposals,
        "venue_preferences": [
            p
            for p in proposals
            if p["status"] == "accepted" and p["proposal_type"] == "venue"
        ],
        "attribute_preferences": [
            p
            for p in proposals
            if p["status"] == "accepted" and p["proposal_type"] == "attribute"
        ],
    }


def register(router: APIRouter, ctx: ApiContext) -> None:
    """Add this module's routes to ``router``."""
    # Shared helpers, bound to local names so the route bodies read naturally.
    Auth = ctx.Auth
    audit = ctx.audit
    invalidate = ctx.invalidate
    store = ctx.store

    def invalidate_learning(db, user_id):
        for upcoming in db.execute(
            "SELECT meal_id FROM participants WHERE user_id=?", (user_id,)
        ).fetchall():
            invalidate(db, upcoming["meal_id"])

    @router.get("/learning")
    def learning(auth=Auth):
        with store.transaction() as db:
            return learning_view(db, auth["user_id"])

    @router.delete("/learning/observations/{observation_id}")
    def delete_observation(observation_id: str, auth=Auth):
        with store.transaction() as db:
            if not remove_observation(db, auth["user_id"], observation_id):
                raise HTTPException(404, "Observation not found")
            invalidate_learning(db, auth["user_id"])
            audit(db, auth["user_id"], "learning_observation_deleted")
            return {"ok": True}

    @router.post("/learning/clear")
    def clear_learning(auth=Auth):
        with store.transaction() as db:
            db.execute("DELETE FROM observations WHERE user_id=?", (auth["user_id"],))
            db.execute(
                "DELETE FROM learning_suggestion_log WHERE user_id=?",
                (auth["user_id"],),
            )
            invalidate_learning(db, auth["user_id"])
            audit(db, auth["user_id"], "learning_cleared")
            return {"ok": True, **learning_view(db, auth["user_id"])}

    @router.post("/learning/proposals/{proposal_id}")
    def review_preference(proposal_id: str, body: PreferenceReview, auth=Auth):
        with store.transaction() as db:
            proposal = db.execute(
                "SELECT * FROM preference_proposals WHERE id=? AND user_id=?",
                (proposal_id, auth["user_id"]),
            ).fetchone()
            if proposal is None:
                raise HTTPException(404, "Preference suggestion not found")
            if not learning_view(db, auth["user_id"])["enabled"]:
                raise HTTPException(409, "Learning is disabled")
            status = "accepted" if body.decision == "accept" else "rejected"
            if proposal["status"] != status:
                db.execute(
                    "UPDATE preference_proposals SET status=?,reviewed_at=? WHERE id=?",
                    (status, stamp(), proposal_id),
                )
                db.execute(
                    "UPDATE learning_suggestion_log SET decision=?,reviewed_at=? WHERE id=?",
                    (body.decision, stamp(), proposal_id),
                )
                invalidate_learning(db, auth["user_id"])
                audit(db, auth["user_id"], "preference_" + status)
            return {"id": proposal_id, "status": status}
