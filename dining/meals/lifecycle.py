"""Pure post-meal lifecycle rules shared by API reads and background workers."""

from datetime import datetime, timedelta

from dining.core.constants import DEFAULT_MEAL_MINUTES, POST_DECISION_STATUSES

POST_MEAL_SOURCE_STATUSES = POST_DECISION_STATUSES


def planned_finish(payload: dict) -> datetime:
    """When the meal is planned to end (start + duration, default 60 minutes)."""
    return datetime.fromisoformat(payload["meal_at"]) + timedelta(
        minutes=payload.get("duration_minutes", DEFAULT_MEAL_MINUTES)
    )


def post_meal_transition(
    status: str, payload: dict, at: datetime
) -> tuple[str, str] | None:
    """Return the next persisted post-meal state, or ``None`` before it is due."""
    if status not in POST_MEAL_SOURCE_STATUSES:
        return None
    finish = planned_finish(payload)
    if at >= finish + timedelta(days=7):
        return "closed", "The seven-day feedback window ended"
    if at >= finish:
        return "awaiting_feedback", "The feedback window is open"
    return None
