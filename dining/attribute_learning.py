"""Explicit attribute-specific feedback signals and reviewable preference proposals."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

VALID_ATTRIBUTES = {
    "taste",
    "value",
    "portion",
    "atmosphere",
    "quietness",
    "service",
    "queue",
    "travel",
}

ATTRIBUTE_DESCRIPTIONS = {
    ("value", "budget_sensitive"): "Prioritize value and budget-friendly meals",
    ("value", "high_value"): "Prefer premium value dining",
    ("portion", "hearty"): "Prefer generous and hearty meal portions",
    ("portion", "light"): "Prefer lighter meal portions",
    ("quietness", "low_noise"): "Prefer quiet, low-noise dining environments",
    ("atmosphere", "lively"): "Prefer lively, vibrant atmospheres",
    ("service", "high_service"): "Prioritize venues known for attentive service",
    ("queue", "low_wait"): "Prioritize venues with minimal queuing or short waits",
    ("travel", "proximity_first"): "Prioritize venues closer to your meeting point",
}


def extract_attribute_signals(feedback_dict: dict[str, Any]) -> dict[str, str]:
    """Extract explicit attribute signals without inferring cuisine or dietary choices."""
    signals: dict[str, str] = {}

    # 1. Direct explicit attribute ratings if provided
    raw_ratings = feedback_dict.get("attribute_ratings") or {}
    if isinstance(raw_ratings, dict):
        for attr, rating in raw_ratings.items():
            if attr in VALID_ATTRIBUTES and rating in {
                "positive",
                "neutral",
                "negative",
            }:
                signals[attr] = rating

    # 2. Explicit cost expectation
    cost_exp = feedback_dict.get("cost_expectation")
    if cost_exp == "higher" and "value" not in signals:
        signals["value"] = "negative"
    elif cost_exp == "lower" and "value" not in signals:
        signals["value"] = "positive"

    # 3. Explicit specific attribute complaints from influences
    influences = feedback_dict.get("influences") or []
    enjoyment = feedback_dict.get("enjoyment")
    rating = feedback_dict.get("rating")
    is_negative = enjoyment == "did_not_enjoy" or (
        isinstance(rating, int) and rating <= 2
    )

    specific_attributes = {"quietness", "service", "queue", "travel", "portion"}
    for inf in influences:
        if inf in specific_attributes and inf not in signals and is_negative:
            signals[inf] = "negative"

    return signals


def propose_attribute_patterns(
    db: Any, user_id: str, new_observation_id: str
) -> list[str]:
    """Propose reviewable attribute-level preferences after 3 consistent supporting visits."""
    from dining.store import decode

    records = db.execute(
        "SELECT id, meal_id, payload, created_at FROM observations WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
        (user_id,),
    ).fetchall()

    if len(records) < 3:
        return []

    # Map attribute -> list of (meal_id, observation_id, signal)
    attribute_events: dict[str, list[tuple[str, str, str]]] = {
        attr: [] for attr in VALID_ATTRIBUTES
    }

    for row in records:
        payload = decode(row["payload"], {})
        # Nonattendance creates no taste or attribute signal
        if not payload.get("visited", True):
            continue
        signals = payload.get("attribute_signals") or {}
        for attr, sig in signals.items():
            if attr in VALID_ATTRIBUTES:
                attribute_events[attr].append((row["meal_id"], row["id"], sig))

    now_iso = datetime.now(timezone.utc).isoformat()
    ninety_days_ago = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    generated_proposals = []

    for attr, events in attribute_events.items():
        if len(events) < 3:
            continue
        recent = events[:3]
        meal_ids = {e[0] for e in recent}
        if len(meal_ids) < 3:
            continue
        signals = {e[2] for e in recent}
        if len(signals) != 1 or "neutral" in signals:
            continue
        signal = signals.pop()

        # Determine proposed value
        if attr == "value":
            proposed_val = "budget_sensitive" if signal == "negative" else "high_value"
        elif attr == "portion":
            proposed_val = "light" if signal == "negative" else "hearty"
        elif attr == "quietness":
            proposed_val = "low_noise"
        elif attr == "atmosphere":
            proposed_val = "lively"
        elif attr == "service":
            proposed_val = "high_service"
        elif attr == "queue":
            proposed_val = "low_wait"
        elif attr == "travel":
            proposed_val = "proximity_first"
        else:
            proposed_val = "favourite" if signal == "positive" else "dislike"

        # Check existing pending or accepted proposals for this attribute
        existing = db.execute(
            "SELECT 1 FROM preference_proposals WHERE user_id=? AND attribute=? AND status IN ('pending', 'accepted')",
            (user_id, attr),
        ).fetchone()
        if existing:
            continue

        # Check suppression from recent rejections
        suppressed = db.execute(
            "SELECT 1 FROM learning_suggestion_log WHERE user_id=? AND outlet_id=? AND decision='reject' AND reviewed_at>=?",
            (user_id, f"attr:{attr}:{proposed_val}", ninety_days_ago),
        ).fetchone()
        if suppressed:
            continue

        prop_id = f"prop_{secrets.token_hex(8)}"
        description = ATTRIBUTE_DESCRIPTIONS.get(
            (attr, proposed_val), f"Learned preference for {attr}"
        )
        source_obs_ids = [e[1] for e in recent]

        db.execute(
            """
            INSERT INTO preference_proposals (
                id, user_id, observation_id, outlet_id, would_repeat,
                status, proposal_type, attribute, proposed_value, description, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prop_id,
                user_id,
                source_obs_ids[0],
                f"attr:{attr}",
                1 if signal == "positive" else 0,
                "pending",
                "attribute",
                attr,
                proposed_val,
                description,
                now_iso,
            ),
        )
        db.executemany(
            "INSERT INTO proposal_sources VALUES (?, ?)",
            [(prop_id, obs_id) for obs_id in source_obs_ids],
        )
        db.execute(
            "INSERT INTO learning_suggestion_log (id, user_id, outlet_id, would_repeat, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                prop_id,
                user_id,
                f"attr:{attr}:{proposed_val}",
                1 if signal == "positive" else 0,
                now_iso,
            ),
        )
        generated_proposals.append(prop_id)

    return generated_proposals
