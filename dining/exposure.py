"""Recommendation exposure, impression, and candidate funnel event tracking."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

EVENT_CANDIDATE_ELIGIBLE = "candidate_eligible"
EVENT_CANDIDATE_SHORTLISTED = "candidate_shortlisted"
EVENT_CARD_SHOWN = "card_shown"
EVENT_CANDIDATE_VOTED_ON = "candidate_voted_on"
EVENT_CANDIDATE_SELECTED = "candidate_selected"
EVENT_CANDIDATE_REJECTED = "candidate_rejected_hard_requirement"

VALID_EVENT_TYPES = {
    EVENT_CANDIDATE_ELIGIBLE,
    EVENT_CANDIDATE_SHORTLISTED,
    EVENT_CARD_SHOWN,
    EVENT_CANDIDATE_VOTED_ON,
    EVENT_CANDIDATE_SELECTED,
    EVENT_CANDIDATE_REJECTED,
}


def record_exposure_event(
    db: Any,
    event_type: str,
    meal_id: str,
    meal_revision: int,
    policy_version: str,
    outlet_id: str,
    option_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> bool:
    """Record an immutable exposure event, deduplicating repeated impressions."""
    if event_type not in VALID_EVENT_TYPES:
        return False

    clean_meta = None
    if metadata:
        allowed_keys = {"category", "choice", "approved", "choice_mode"}
        clean_meta = {k: v for k, v in metadata.items() if k in allowed_keys}

    encoded_meta = (
        json.dumps(clean_meta, sort_keys=True, separators=(",", ":"))
        if clean_meta
        else None
    )
    now_stamp = created_at or datetime.now(timezone.utc).isoformat()
    event_id = uuid.uuid4().hex
    dedup_key = f"{event_type}:{meal_id}:{meal_revision}:{user_id or ''}:{outlet_id}:{option_id or ''}"

    cursor = db.execute(
        """
        INSERT OR IGNORE INTO exposure_events (
            id, event_type, meal_id, meal_revision, policy_version,
            outlet_id, option_id, user_id, metadata, dedup_key, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            meal_id,
            meal_revision,
            policy_version or "1.0",
            outlet_id,
            option_id,
            user_id,
            encoded_meta,
            dedup_key,
            now_stamp,
        ),
    )
    return cursor.rowcount > 0


def record_generation_exposures(
    db: Any, meal_id: str, meal_revision: int, result: dict[str, Any]
) -> None:
    policy_version = result.get("policy_version", "1.0")
    exposure_data = result.get("_exposure_candidates", {})

    for rej in exposure_data.get("rejected", []):
        record_exposure_event(
            db,
            EVENT_CANDIDATE_REJECTED,
            meal_id=meal_id,
            meal_revision=meal_revision,
            policy_version=policy_version,
            outlet_id=rej["outlet_id"],
            metadata={"category": rej.get("category", "hard_requirement")},
        )

    for elig in exposure_data.get("eligible", []):
        record_exposure_event(
            db,
            EVENT_CANDIDATE_ELIGIBLE,
            meal_id=meal_id,
            meal_revision=meal_revision,
            policy_version=policy_version,
            outlet_id=elig["outlet_id"],
            option_id=elig.get("option_id"),
        )

    for opt in result.get("options", []):
        outlet_id = opt.get("outlet_id")
        option_id = opt.get("id") or opt.get("option_id")
        if outlet_id:
            record_exposure_event(
                db,
                EVENT_CANDIDATE_SHORTLISTED,
                meal_id=meal_id,
                meal_revision=meal_revision,
                policy_version=policy_version,
                outlet_id=outlet_id,
                option_id=option_id,
            )


def record_card_impressions(
    db: Any,
    meal_id: str,
    meal_revision: int,
    policy_version: str,
    user_id: str,
    options: list[dict[str, Any]],
) -> None:
    for opt in options:
        outlet_id = opt.get("outlet_id")
        option_id = opt.get("id") or opt.get("option_id")
        if outlet_id and option_id:
            record_exposure_event(
                db,
                EVENT_CARD_SHOWN,
                meal_id=meal_id,
                meal_revision=meal_revision,
                policy_version=policy_version,
                outlet_id=outlet_id,
                option_id=option_id,
                user_id=user_id,
            )


def get_exposure_metrics(db: Any, meal_id: str | None = None) -> dict[str, Any]:
    where = "WHERE meal_id = ?" if meal_id else ""
    params = (meal_id,) if meal_id else ()

    rows = db.execute(
        f"SELECT event_type, metadata, COUNT(*) as cnt FROM exposure_events {where} GROUP BY event_type, metadata",
        params,
    ).fetchall()

    counts: dict[str, int] = {}
    positive_votes = 0
    for r in rows:
        etype = r["event_type"]
        cnt = r["cnt"]
        counts[etype] = counts.get(etype, 0) + cnt
        if etype == EVENT_CANDIDATE_VOTED_ON and r["metadata"]:
            try:
                meta = json.loads(r["metadata"])
                if meta.get("approved") is True or meta.get("choice") == "works":
                    positive_votes += cnt
            except (ValueError, TypeError):
                pass

    eligible = counts.get(EVENT_CANDIDATE_ELIGIBLE, 0)
    rejected = counts.get(EVENT_CANDIDATE_REJECTED, 0)
    total_evaluated = eligible + rejected
    shortlisted = counts.get(EVENT_CANDIDATE_SHORTLISTED, 0)
    impressions = counts.get(EVENT_CARD_SHOWN, 0)
    votes = counts.get(EVENT_CANDIDATE_VOTED_ON, 0)
    selections = counts.get(EVENT_CANDIDATE_SELECTED, 0)

    return {
        "eligible_count": eligible,
        "rejected_count": rejected,
        "total_evaluated": total_evaluated,
        "eligibility_rate": (eligible / total_evaluated)
        if total_evaluated > 0
        else 0.0,
        "shortlisted_count": shortlisted,
        "impressions_count": impressions,
        "votes_count": votes,
        "positive_votes_count": positive_votes,
        "selections_count": selections,
        "shortlist_acceptance_rate": (selections / shortlisted)
        if shortlisted > 0
        else 0.0,
        "card_acceptance_rate": (positive_votes / impressions)
        if impressions > 0
        else 0.0,
        "vote_approval_rate": (positive_votes / votes) if votes > 0 else 0.0,
    }
