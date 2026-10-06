"""SQLite persistence for the local group-dining pilot.

All writes use short serialized transactions. Agent work never runs under this lock.
The database contains private data: this local pilot requires protected storage.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_PROFILE = {
    "allergy_status": "unknown",
    "allergens": [],
    "dietary_requirements": [],
    "halal_policy": "unknown",
    "cuisines": [],
    "spice": "any",
    "memory_enabled": False,
    "requirements_reviewed": False,
    "sensitive_data_consent": False,
    "max_budget": None,
    "mobility_mode": "drive",
    "accessibility_requirements": [],
    "language": "en",
    "taste_preferences": {},
}


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def decode(value: str | None, default=None):
    return json.loads(value) if value is not None else default


class DiningStore:
    def __init__(self, path: str | Path):
        path = str(path)
        if path != ":memory:":
            database_path = Path(path)
            # mkdir leaves an existing directory's permissions untouched. A new
            # application data directory should be private from its creation.
            database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "posix":
                # Pre-create before SQLite opens the file, avoiding an initial
                # world-readable window under the caller's default umask.
                flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(database_path, flags, 0o600)
                try:
                    os.fchmod(descriptor, 0o600)
                finally:
                    os.close(descriptor)
        self.connection = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        with self._lock:
            self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,
              password_hash TEXT NOT NULL,profile TEXT NOT NULL,
              profile_revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              csrf_token TEXT NOT NULL,expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
              bucket TEXT NOT NULL,attempted_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS login_bucket ON login_attempts(bucket,attempted_at);
            CREATE TABLE IF NOT EXISTS rooms (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,
              owner_id TEXT REFERENCES users(id) ON DELETE SET NULL,
              archived INTEGER NOT NULL DEFAULT 0,invite_hash TEXT,invite_expires REAL,
              membership_revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS members (
              room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              joined_at TEXT NOT NULL,PRIMARY KEY(room_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS meals (
              id TEXT PRIMARY KEY,room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
              organizer_id TEXT REFERENCES users(id) ON DELETE SET NULL,
              payload TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'collecting',result TEXT,result_revision INTEGER,
              frozen_participants TEXT,decision TEXT,created_at TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              UNIQUE(room_id,organizer_id,idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS generation_jobs (
              id TEXT PRIMARY KEY,meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              context_revision INTEGER NOT NULL,evidence_revision TEXT NOT NULL,
              policy_version TEXT NOT NULL,membership_revision INTEGER NOT NULL,
              input_fingerprint TEXT,
              status TEXT NOT NULL DEFAULT 'queued',attempt_count INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL DEFAULT 3,next_attempt_at REAL NOT NULL DEFAULT 0,
              lease_token TEXT,lease_until REAL,error_code TEXT,
              created_at TEXT NOT NULL,completed_at TEXT,
              UNIQUE(meal_id,context_revision,evidence_revision,policy_version)
            );
            CREATE TABLE IF NOT EXISTS generation_inputs (
              job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              profile_revision INTEGER NOT NULL,response_revision INTEGER NOT NULL,
              PRIMARY KEY(job_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS generation_attempts (
              job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
              number INTEGER NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,
              completed_at TEXT,error_code TEXT,duration_ms INTEGER,metadata TEXT,
              PRIMARY KEY(job_id,number)
            );
            CREATE TABLE IF NOT EXISTS participants (
              meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              attendance TEXT NOT NULL DEFAULT 'pending',response TEXT,
              response_revision INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(meal_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS preference_requests (
              id TEXT PRIMARY KEY,meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              requested_at REAL NOT NULL,status TEXT NOT NULL,metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS preference_request_owner ON preference_requests(user_id,meal_id);
            CREATE TABLE IF NOT EXISTS adaptive_questions (
              id TEXT PRIMARY KEY,meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              issued_at REAL NOT NULL,meal_revision INTEGER NOT NULL,
              response_revision INTEGER NOT NULL,choices TEXT NOT NULL,status TEXT NOT NULL,
              UNIQUE(meal_id,user_id)
            );
            CREATE INDEX IF NOT EXISTS adaptive_question_owner ON adaptive_questions(user_id,meal_id);
            CREATE TABLE IF NOT EXISTS votes (
              meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              option_id TEXT NOT NULL,revision INTEGER NOT NULL,approve INTEGER NOT NULL,
              PRIMARY KEY(meal_id,user_id,option_id)
            );
            CREATE TABLE IF NOT EXISTS feedback (
              meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              payload TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(meal_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS observations (
              id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              meal_id TEXT REFERENCES meals(id) ON DELETE CASCADE,payload TEXT NOT NULL,
              created_at TEXT NOT NULL,UNIQUE(user_id,meal_id)
            );
            CREATE TABLE IF NOT EXISTS notifications (
              id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              room_id TEXT REFERENCES rooms(id) ON DELETE CASCADE,
              meal_id TEXT REFERENCES meals(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL,
              read INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS audit_events (
              id TEXT PRIMARY KEY,actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
              room_id TEXT REFERENCES rooms(id) ON DELETE CASCADE,
              meal_id TEXT REFERENCES meals(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delegations (
              meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              revision INTEGER NOT NULL,option_ids TEXT NOT NULL,created_at TEXT NOT NULL,
              PRIMARY KEY(meal_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS manual_acknowledgements (
              meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              revision INTEGER NOT NULL,created_at TEXT NOT NULL,
              PRIMARY KEY(meal_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS preference_proposals (
              id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              observation_id TEXT NOT NULL UNIQUE REFERENCES observations(id) ON DELETE CASCADE,
              outlet_id TEXT NOT NULL,would_repeat INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL,reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS proposal_sources (
              proposal_id TEXT NOT NULL REFERENCES preference_proposals(id) ON DELETE CASCADE,
              observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
              PRIMARY KEY(proposal_id,observation_id)
            );
            CREATE TABLE IF NOT EXISTS learning_suggestion_log (
              id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              outlet_id TEXT NOT NULL,would_repeat INTEGER NOT NULL,created_at TEXT NOT NULL,
              decision TEXT,reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS data_error_reports (
              id TEXT PRIMARY KEY,meal_id TEXT REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              outlet_id TEXT NOT NULL,category TEXT NOT NULL,description TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'investigating',created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS data_error_reports_meal ON data_error_reports(meal_id);
            CREATE INDEX IF NOT EXISTS data_error_reports_user ON data_error_reports(user_id);
            CREATE TABLE IF NOT EXISTS recommendation_archives (
              id TEXT PRIMARY KEY,meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS recommendation_archives_meal ON recommendation_archives(meal_id,revision);
            CREATE TABLE IF NOT EXISTS meal_origins (
              meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              origin_mode TEXT NOT NULL,
              latitude REAL,
              longitude REAL,
              approximate_area TEXT,
              route_consent INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(meal_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS meal_origins_user ON meal_origins(user_id);
            CREATE TABLE IF NOT EXISTS preparation_confirmations (
              id TEXT PRIMARY KEY,meal_id TEXT NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
              outlet_id TEXT NOT NULL,requirement_category TEXT NOT NULL,
              confirmed_by TEXT NOT NULL,confirmation_channel TEXT NOT NULL,
              confirmed_at TEXT NOT NULL,expires_at TEXT NOT NULL,
              exact_bounded_claim TEXT NOT NULL,created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS prep_conf_meal ON preparation_confirmations(meal_id,outlet_id);
            """)
            # Additive migrations keep existing accounts, responses and historical votes.
            additions = {
                "generation_jobs": {"input_fingerprint": "TEXT"},
                "votes": {"choice": "TEXT", "reason": "TEXT NOT NULL DEFAULT ''"},
                "meals": {
                    "original_answer_by": "TEXT",
                    "original_decision_by": "TEXT",
                    "reconfirmation_required": "INTEGER NOT NULL DEFAULT 0",
                    "lifecycle_reason": "TEXT",
                    "original_invited_count": "INTEGER",
                    "manual_plan": "TEXT",
                },
            }
            for table, columns in additions.items():
                present = {
                    row["name"]
                    for row in self.connection.execute(f"PRAGMA table_info({table})")
                }
                for column, definition in columns.items():
                    if column not in present:
                        self.connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                        )
            self.connection.execute(
                "UPDATE votes SET choice=CASE WHEN approve=1 THEN 'works' ELSE 'prefer_another' END WHERE choice IS NULL"
            )
            self.connection.execute(
                "UPDATE meals SET original_answer_by=COALESCE(original_answer_by,json_extract(payload,'$.answer_by')),original_decision_by=COALESCE(original_decision_by,json_extract(payload,'$.decision_by'))"
            )
            # This pilot runs one application process. An interrupted generation must
            # become retryable after restart rather than remain "generating" forever.
            self.connection.execute(
                "UPDATE meals SET status='collecting',revision=revision+1,result=NULL,result_revision=NULL,frozen_participants=NULL,decision=NULL WHERE status='generating' AND NOT EXISTS (SELECT 1 FROM generation_jobs j WHERE j.meal_id=meals.id AND j.context_revision=meals.revision AND j.status IN ('queued','running','retry_wait'))"
            )
            self.connection.execute(
                "DELETE FROM votes WHERE revision != (SELECT revision FROM meals WHERE meals.id=votes.meal_id)"
            )
            self.connection.execute(
                "DELETE FROM delegations WHERE revision != (SELECT revision FROM meals WHERE meals.id=delegations.meal_id)"
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()
