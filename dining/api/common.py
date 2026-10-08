"""Small primitives shared by the API modules: clock, ids, hashing and session settings."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from pwdlib import PasswordHash

from dining.core.constants import SECONDS_PER_DAY

COOKIE = "dining_session"
SESSION_SECONDS = 7 * SECONDS_PER_DAY
PASSWORD_HASHER = PasswordHash.recommended()
# A constant hash makes unknown-account login perform the same expensive check.
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("not-a-real-account-password")


def system_now() -> datetime:
    """The real current time in UTC."""
    return datetime.now(timezone.utc)


def now() -> datetime:
    """The API clock.

    It always reads ``dining.api.now`` at call time, so a test that freezes the clock
    there (``monkeypatch.setattr("dining.api.now", ...)``) freezes it for every route
    module too.
    """
    from dining import api

    return api.now()


def stamp() -> str:
    """The API clock as an ISO-8601 string, for ``created_at`` style columns."""
    return now().isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def digest(value: str) -> str:
    """SHA-256 hex digest (session tokens and invite codes are stored only hashed)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
