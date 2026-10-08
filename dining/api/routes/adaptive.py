"""Adaptive follow-up question routes (PRD M09): one optional question per participant."""

from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from dining.api.common import (
    new_id,
    now,
)
from dining.api.context import ApiContext
from dining.api.schemas import (
    AdaptiveQuestionAnswer,
    AdaptiveQuestionRequest,
)
from dining.core.store import decode, encode
from dining.recommendation.adaptive import build_m09_question


def register(router: APIRouter, ctx: ApiContext) -> None:
    """Add this module's routes to ``router``."""
    # Shared helpers, bound to local names so the route bodies read naturally.
    Auth = ctx.Auth
    audit = ctx.audit
    authenticate = ctx.authenticate
    invalidate = ctx.invalidate
    meal_member = ctx.meal_member
    meal_view = ctx.meal_view
    recommend = ctx.recommend
    snapshot_for = ctx.generation.snapshot_for
    store = ctx.store

    def adaptive_context(db, meal_id, user_id, body):
        meal = meal_member(db, meal_id, user_id, mutable=True)
        payload = decode(meal["payload"])
        if meal["decision"] or datetime.fromisoformat(payload["decision_by"]) <= now():
            raise HTTPException(
                409, "This meal no longer accepts an optional follow-up"
            )
        participant = db.execute(
            "SELECT response,response_revision FROM participants WHERE meal_id=? AND user_id=?",
            (meal_id, user_id),
        ).fetchone()
        if (
            meal["revision"] != body.expected_revision
            or participant["response_revision"] != body.expected_response_revision
        ):
            raise HTTPException(
                409, "The meal or your answer changed. Refresh before continuing"
            )
        response = decode(participant["response"], {})
        if not response.get("ready") or response.get("attendance") != "join":
            raise HTTPException(
                409, "Finish your core check-in before an optional follow-up"
            )
        included = [
            row["user_id"]
            for row in db.execute(
                "SELECT user_id,response FROM participants WHERE meal_id=? AND attendance='join' ORDER BY user_id",
                (meal_id,),
            )
            if decode(row["response"], {}).get("ready")
            and decode(row["response"], {}).get("requirements_confirmed")
        ]
        if len(included) < 2:
            raise HTTPException(
                409, "Wait until at least two people finish checking in"
            )
        return meal, participant, snapshot_for(db, meal, included)

    @router.post("/meals/{meal_id}/adaptive-question")
    def issue_adaptive_question(
        meal_id: str, body: AdaptiveQuestionRequest, request: Request, auth=Auth
    ):
        with store.transaction() as db:
            meal, participant, snapshot = adaptive_context(
                db, meal_id, auth["user_id"], body
            )
            existing = db.execute(
                "SELECT * FROM adaptive_questions WHERE meal_id=? AND user_id=? ORDER BY issued_at DESC LIMIT 1",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if existing:
                if (
                    existing["status"] == "issued"
                    and existing["meal_revision"] == meal["revision"]
                    and existing["response_revision"]
                    == participant["response_revision"]
                ):
                    return {
                        "status": "available",
                        "request_id": existing["id"],
                        "question_id": "M09",
                        "question_version": "adaptive-m09-v1",
                        "prompt": "Which sounds better for this meal?",
                        "dimension": "cuisines",
                        "choices": decode(existing["choices"], []),
                        "optional": True,
                        "purpose": "These choices lead to different preliminary shortlist orders.",
                    }
                if existing["status"] == "issued":
                    db.execute(
                        "UPDATE adaptive_questions SET status='stale' WHERE id=?",
                        (existing["id"],),
                    )
                    return {"status": "already_stale", "question_id": "M09"}
                return {"status": f"already_{existing['status']}", "question_id": "M09"}

        deterministic = getattr(recommend, "recommender", None)
        question = (
            build_m09_question(deterministic, snapshot, auth["user_id"])
            if deterministic is not None
            else None
        )
        authenticate(request)
        with store.transaction() as db:
            meal, participant, _ = adaptive_context(db, meal_id, auth["user_id"], body)
            existing = db.execute(
                "SELECT status FROM adaptive_questions WHERE meal_id=? AND user_id=? LIMIT 1",
                (meal_id, auth["user_id"]),
            ).fetchone()
            if existing:
                return {"status": f"already_{existing['status']}", "question_id": "M09"}
            if question is None:
                return {
                    "status": "not_needed",
                    "question_id": "M09",
                    "reason": "No optional choice was found that would change the preliminary order.",
                }
            request_id = new_id()
            db.execute(
                "INSERT INTO adaptive_questions VALUES(?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    meal_id,
                    auth["user_id"],
                    time.time(),
                    meal["revision"],
                    participant["response_revision"],
                    encode(question["choices"]),
                    "issued",
                ),
            )
            audit(
                db,
                auth["user_id"],
                "adaptive_question_issued",
                meal["room_id"],
                meal_id,
            )
        return {**question, "status": "available", "request_id": request_id}

    @router.post("/meals/{meal_id}/adaptive-question/{request_id}")
    def answer_adaptive_question(
        meal_id: str, request_id: str, body: AdaptiveQuestionAnswer, auth=Auth
    ):
        with store.transaction() as db:
            meal = meal_member(db, meal_id, auth["user_id"], mutable=True)
            if datetime.fromisoformat(decode(meal["payload"])["decision_by"]) <= now():
                raise HTTPException(
                    409, "This meal no longer accepts an optional follow-up"
                )
            question = db.execute(
                "SELECT * FROM adaptive_questions WHERE id=? AND meal_id=? AND user_id=?",
                (request_id, meal_id, auth["user_id"]),
            ).fetchone()
            if not question:
                raise HTTPException(404, "Optional question not found")
            if question["status"] != "issued":
                raise HTTPException(409, "This optional question was already completed")
            participant = db.execute(
                "SELECT response,response_revision FROM participants WHERE meal_id=? AND user_id=?",
                (meal_id, auth["user_id"]),
            ).fetchone()
            stale = (
                meal["revision"] != question["meal_revision"]
                or participant["response_revision"] != question["response_revision"]
            )
            if stale:
                db.execute(
                    "UPDATE adaptive_questions SET status='stale' WHERE id=?",
                    (request_id,),
                )
            elif body.skip:
                db.execute(
                    "UPDATE adaptive_questions SET status='skipped' WHERE id=?",
                    (request_id,),
                )
                audit(
                    db,
                    auth["user_id"],
                    "adaptive_question_skipped",
                    meal["room_id"],
                    meal_id,
                )
                return meal_view(db, meal, auth["user_id"])
            else:
                choices = decode(question["choices"], [])
                if body.choice not in choices:
                    raise HTTPException(
                        422, "Choose one of the current offered answers"
                    )
                response = decode(participant["response"], {})
                response.update(taste_input_mode="structured", cuisines=[body.choice])
                db.execute(
                    "UPDATE participants SET response=?,response_revision=response_revision+1 WHERE meal_id=? AND user_id=?",
                    (encode(response), meal_id, auth["user_id"]),
                )
                db.execute(
                    "UPDATE adaptive_questions SET status='answered' WHERE id=?",
                    (request_id,),
                )
                invalidate(db, meal_id)
                audit(
                    db,
                    auth["user_id"],
                    "adaptive_question_answered",
                    meal["room_id"],
                    meal_id,
                )
                return meal_view(
                    db,
                    db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone(),
                    auth["user_id"],
                )
        raise HTTPException(
            409, "The meal or your answer changed. Refresh before continuing"
        )
