"""Pure post-meal lifecycle rules shared by API reads and background workers."""

from datetime import datetime, timedelta

POST_MEAL_SOURCE_STATUSES = {"selected", "manual_selected", "awaiting_feedback"}


def planned_finish(payload: dict) -> datetime:
    return datetime.fromisoformat(payload["meal_at"]) + timedelta(
        minutes=payload.get("duration_minutes", 60)
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
