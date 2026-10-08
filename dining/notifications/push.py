"""Privacy-safe web push subscription and payload handling.

Push payloads must never contain private dietary requirements, allergens, or GPS coordinates.
Inbox remains the fallback when push is unavailable or denied.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

SAFE_TEMPLATES = {
    "meal_invitation": "You've been invited to check in for a group meal.",
    "meal_shortlist": "Your table's recommendation shortlist is ready for review.",
    "meal_decision": "Your table has confirmed a dining choice.",
    "checkin_reminder": "Reminder: Please complete your check-in before the cutoff.",
    "feedback_prompt": "How was your meal? Feedback is open for your table.",
}

SENSITIVE_TEXT_PATTERN = re.compile(
    r"\b(?:allergy|allergies|allergic|peanut|nuts?|shellfish|halal|haram|pork|gluten|celiac|egg|dairy|beef|gps|coordinates?)\b|\d{1,3}\.\d{3,}",
    re.IGNORECASE,
)


def format_safe_notification(kind: str, raw_message: str) -> str:
    """Format group-safe generic text, stripping sensitive dietary/location content."""
    template = SAFE_TEMPLATES.get(kind)
    if template:
        return template

    sanitized = SENSITIVE_TEXT_PATTERN.sub("[redacted]", raw_message)
    return sanitized


def create_push_payload(
    kind: str,
    safe_message: str,
    meal_id: str | None = None,
    room_id: str | None = None,
) -> dict[str, Any]:
    """Create a minimal notification payload containing only structural metadata."""
    safe_body = format_safe_notification(kind, safe_message)

    return {
        "title": "Makan Together",
        "body": safe_body,
        "icon": "/assets/favicon.svg",
        "badge": "/assets/favicon.svg",
        "data": {
            "kind": kind,
            "meal_id": meal_id,
            "room_id": room_id,
        },
    }


class PushSubscriptionManager:
    """Web Push subscriptions for each user's devices."""

    @staticmethod
    def register(
        db,
        user_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Save an HTTPS push subscription; a device moves to the newly signed-in user."""
        if not endpoint.startswith("https://"):
            raise ValueError("Push endpoint must use HTTPS")
        if not p256dh or not auth:
            raise ValueError("Push keys p256dh and auth are required")

        # Account switching: if endpoint was previously registered to another user on the same device,
        # remove it from the old user so they don't receive future notifications
        db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint=? AND user_id!=?",
            (endpoint, user_id),
        )

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sub_id = str(uuid.uuid4())

        db.execute(
            """
            INSERT INTO push_subscriptions(
                id, user_id, endpoint, p256dh, auth, user_agent, status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                p256dh=excluded.p256dh,
                auth=excluded.auth,
                user_agent=excluded.user_agent,
                status='active',
                updated_at=excluded.updated_at
            """,
            (sub_id, user_id, endpoint, p256dh, auth, user_agent, now, now),
        )

        return {
            "id": sub_id,
            "endpoint": endpoint,
            "status": "active",
            "created_at": now,
        }

    @staticmethod
    def unsubscribe(db, user_id: str, endpoint: str) -> bool:
        """Remove one of the user's subscriptions. True if it existed."""
        cursor = db.execute(
            "DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?",
            (user_id, endpoint),
        )
        return cursor.rowcount > 0

    @staticmethod
    def list_user_subscriptions(db, user_id: str) -> list[dict[str, Any]]:
        """The user's active subscriptions (no keys)."""
        rows = db.execute(
            "SELECT id, endpoint, status, created_at FROM push_subscriptions WHERE user_id=? AND status='active'",
            (user_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "endpoint": r["endpoint"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
