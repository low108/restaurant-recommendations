"""Versioned, purpose-specific consent management.

Separates terms, sensitive dietary requirements, location routing, taste learning,
model processing, and analytics into versioned consent records.
Absence of consent disables processing. A new notice version prompts review.
Withdrawal stops future processing without rewriting audit history.
"""

from __future__ import annotations

import enum
import time
import uuid
from typing import Any, Literal

ConsentDecision = Literal["accepted", "declined", "withdrawn"]


class ConsentPurpose(str, enum.Enum):
    TERMS = "terms"
    SENSITIVE_DIETARY_DATA = "sensitive_dietary_data"
    LOCATION_ROUTING = "location_routing"
    LEARNING = "learning"
    MODEL_PROCESSING = "model_processing"
    ANALYTICS = "analytics"


CURRENT_NOTICES: dict[str, str] = {
    ConsentPurpose.TERMS.value: "2026-10-06.1",
    ConsentPurpose.SENSITIVE_DIETARY_DATA.value: "2026-10-06.1",
    ConsentPurpose.LOCATION_ROUTING.value: "2026-10-06.1",
    ConsentPurpose.LEARNING.value: "2026-10-06.1",
    ConsentPurpose.MODEL_PROCESSING.value: "2026-10-06.1",
    ConsentPurpose.ANALYTICS.value: "2026-10-06.1",
}


class ConsentManager:
    @staticmethod
    def record_consent(
        db,
        user_id: str,
        purpose: str,
        notice_version: str,
        decision: ConsentDecision,
        source_interface: str = "web_settings",
    ) -> dict[str, Any]:
        if purpose not in CURRENT_NOTICES:
            raise ValueError(f"Unknown consent purpose: {purpose}")
        if decision not in ("accepted", "declined", "withdrawn"):
            raise ValueError(f"Invalid consent decision: {decision}")

        record_id = str(uuid.uuid4())
        now = time.time()
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

        db.execute(
            """
            INSERT INTO consent_records (
                id, user_id, notice_version, purpose, decision,
                source_interface, timestamp, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                user_id,
                notice_version,
                purpose,
                decision,
                source_interface,
                now,
                created_at,
            ),
        )

        current_ver = CURRENT_NOTICES.get(purpose, "")
        needs_review = decision == "accepted" and notice_version != current_ver
        active = decision == "accepted" and not needs_review

        return {
            "id": record_id,
            "purpose": purpose,
            "notice_version": notice_version,
            "decision": decision,
            "source_interface": source_interface,
            "needs_review": needs_review,
            "active": active,
            "created_at": created_at,
        }

    @staticmethod
    def get_consent_status(db, user_id: str) -> dict[str, Any]:
        consents = {}
        for purpose, current_ver in CURRENT_NOTICES.items():
            row = db.execute(
                """
                SELECT * FROM consent_records
                WHERE user_id=? AND purpose=?
                ORDER BY timestamp DESC, rowid DESC LIMIT 1
                """,
                (user_id, purpose),
            ).fetchone()

            if not row:
                consents[purpose] = {
                    "active": False,
                    "decision": "none",
                    "accepted_version": None,
                    "needs_review": False,
                }
            else:
                decision = row["decision"]
                version = row["notice_version"]
                needs_review = decision == "accepted" and version != current_ver
                active = decision == "accepted" and not needs_review
                consents[purpose] = {
                    "active": active,
                    "decision": decision,
                    "accepted_version": version,
                    "needs_review": needs_review,
                }

        return {
            "notices": CURRENT_NOTICES,
            "consents": consents,
        }

    @staticmethod
    def has_consent(db, user_id: str, purpose: str) -> bool:
        current_ver = CURRENT_NOTICES.get(purpose)
        if not current_ver:
            return False
        row = db.execute(
            """
            SELECT decision, notice_version FROM consent_records
            WHERE user_id=? AND purpose=?
            ORDER BY timestamp DESC, rowid DESC LIMIT 1
            """,
            (user_id, purpose),
        ).fetchone()
        if not row:
            return False
        return row["decision"] == "accepted" and row["notice_version"] == current_ver
