"""Durable, lease-fenced jobs. Private snapshots live in memory for one attempt only."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from .inference import MODEL_PATTERN, ROLE
from .ranking import FEATURE_VERSION, ONTOLOGY_VERSION, POLICY_VERSION
from .store import decode, encode

ACTIVE = {"queued", "running", "retry_wait"}


def timestamp(value=None):
    return datetime.fromtimestamp(
        time.time() if value is None else value, timezone.utc
    ).isoformat()


def evidence_identity(recommend, at=None):
    """Include permitted source availability so crossing an expiry changes the identity."""
    explicit = getattr(recommend, "evidence_revision", None)
    if callable(explicit):
        return hashlib.sha256(str(explicit()).encode()).hexdigest()
    ranker = getattr(recommend, "recommender", recommend)
    catalog = getattr(ranker, "catalog", None)
    if catalog is None:
        # Adapters without a catalog can supply evidence_revision; this fallback is
        # only suitable for the unchanged, local deterministic callback contract.
        return "local-adapter-v1"
    at = at or datetime.now(timezone.utc)
    data = {
        "catalog": catalog.model_dump(mode="json"),
        "inference": getattr(getattr(recommend, "settings", None), "signature", None),
        "current_sources": [
            s.source_id for s in catalog.sources if s.usable_for("display", at)
        ],
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot_identity(snapshot):
    # This digest is internal and erased when an attempt becomes terminal. It
    # catches permitted history/learning changes that are outside profile CAS.
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def policy_identity():
    return f"{POLICY_VERSION}:{FEATURE_VERSION}:{ONTOLOGY_VERSION}"


def result_metadata(result):
    """An allowlist protects historical receipts even from malformed adapter output."""
    agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}

    def count(value):
        return value if type(value) is int and 0 <= value <= 1_000_000_000 else None

    def duration(value):
        return (
            value
            if type(value) in {int, float}
            and math.isfinite(value)
            and 0 <= value <= 86_400_000
            else None
        )

    statuses = {
        "ok",
        "skipped",
        "completed",
        "shortlisted",
        "needs_verification",
        "needs_input",
        "no_options",
        "validated",
        "template_fallback",
        "disabled",
        "not_configured",
        "skipped_no_options",
        "provider_error",
        "invalid_output",
    }
    stage_names = {
        "before_agent",
        "check_and_rank",
        "before_model",
        "after_model",
        "after_agent",
    }
    stages = []
    raw_stages = agent.get("stages", [])
    for stage in raw_stages[:10] if isinstance(raw_stages, list) else []:
        if (
            not isinstance(stage, dict)
            or not isinstance(stage.get("stage"), str)
            or stage.get("stage") not in stage_names
        ):
            continue
        stages.append(
            {
                "stage": stage["stage"],
                "status": stage.get("status")
                if isinstance(stage.get("status"), str)
                and stage.get("status") in statuses
                else "unknown",
                "duration_ms": duration(stage.get("duration_ms")),
                "projection": "structural_only",
            }
        )
    run_id = result.get("run_id")
    inference_fields = {
        "provider": {"disabled", "ilmu", "ollama", "openai_compatible"},
        "configuration_status": {"disabled", "ready", "not_configured"},
        "role": {ROLE},
        "model_status": {
            "disabled",
            "not_configured",
            "skipped_no_options",
            "validated",
            "template_fallback",
        },
        "inference_status": {
            "disabled",
            "not_configured",
            "skipped_no_options",
            "validated",
            "provider_error",
            "invalid_output",
        },
        "model_error_code": {
            "unsupported_provider",
            "model_required",
            "invalid_model",
            "invalid_endpoint",
            "api_key_required",
            "invalid_limits",
            "provider_error",
            "invalid_output",
        },
    }
    inference = {
        key: agent[key]
        if isinstance(agent.get(key), str) and agent[key] in allowed
        else (None if key == "model_error_code" else "unknown")
        for key, allowed in inference_fields.items()
    }
    for key in ("model_id", "returned_model_id"):
        value = agent.get(key)
        inference[key] = (
            value if isinstance(value, str) and MODEL_PATTERN.fullmatch(value) else None
        )
    return {
        **inference,
        "result_status": result.get("status")
        if result.get("status") in statuses
        else "unknown",
        "option_count": len(result.get("options", [])),
        "run_id": run_id
        if isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{32}", run_id)
        else None,
        "input_tokens": count(agent.get("input_tokens")),
        "output_tokens": count(agent.get("output_tokens")),
        "total_tokens": count(agent.get("total_tokens")),
        "cached_tokens": count(agent.get("cached_tokens")),
        "estimated_cost": agent.get("estimated_cost")
        if isinstance(agent.get("estimated_cost"), (int, float))
        and not isinstance(agent.get("estimated_cost"), bool)
        and agent.get("estimated_cost") >= 0
        else None,
        "currency": agent.get("currency")
        if isinstance(agent.get("currency"), str)
        else None,
        "price_version": agent.get("price_version")
        if isinstance(agent.get("price_version"), str)
        else None,
        "usage_source": agent.get("usage_source")
        if isinstance(agent.get("usage_source"), str)
        and agent.get("usage_source")
        in {"provider", "estimated", "unknown", "not_called"}
        else "unknown",
        "model_calls": count(agent.get("model_calls")),
        "tool_calls": count(agent.get("tool_calls")),
        "elapsed_ms": duration(agent.get("elapsed_ms")),
        "stages": stages,
    }


def job_view(db, job):
    if job is None:
        return None
    attempts = [
        {
            "number": a["number"],
            "status": a["status"],
            "started_at": a["started_at"],
            "completed_at": a["completed_at"],
            "error_code": a["error_code"],
            "duration_ms": a["duration_ms"],
            "metadata": decode(a["metadata"], {}),
        }
        for a in db.execute(
            "SELECT * FROM generation_attempts WHERE job_id=? ORDER BY number",
            (job["id"],),
        )
    ]
    return {
        "id": job["id"],
        "status": job["status"],
        "stage": {"running": "checking_options", "published": "complete"}.get(
            job["status"], job["status"]
        ),
        "context_revision": job["context_revision"],
        "evidence_revision": job["evidence_revision"],
        "policy_version": job["policy_version"],
        "attempt_count": job["attempt_count"],
        "max_attempts": job["max_attempts"],
        "retryable": job["status"] in {"queued", "retry_wait"}
        and job["attempt_count"] < job["max_attempts"],
        "next_attempt_at": timestamp(job["next_attempt_at"])
        if job["status"] == "retry_wait"
        else None,
        "error_code": job["error_code"],
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
        "attempts": attempts,
    }


def supersede_jobs(db, meal_id, at=None):
    """Cancel only unfinished attempts; completed receipts are never rewritten."""
    for job in db.execute(
        "SELECT id FROM generation_jobs WHERE meal_id=? AND status IN ('queued','running','retry_wait')",
        (meal_id,),
    ).fetchall():
        db.execute(
            "UPDATE generation_attempts SET status='superseded',completed_at=?,error_code='CONTEXT_CHANGED' WHERE job_id=? AND status='running'",
            (timestamp(at), job["id"]),
        )
        db.execute(
            "UPDATE generation_jobs SET status='superseded',completed_at=?,error_code='CONTEXT_CHANGED',lease_token=NULL,lease_until=NULL,input_fingerprint=NULL WHERE id=?",
            (timestamp(at), job["id"]),
        )
        db.execute("DELETE FROM generation_inputs WHERE job_id=?", (job["id"],))


class Superseded(Exception):
    """The current authorization, context or evidence no longer matches the job."""


class GenerationWorker:
    def __init__(
        self,
        store,
        prepare,
        run,
        publish,
        *,
        clock=time.time,
        max_attempts=3,
        timeout_seconds=60,
        lease_seconds=75,
        retry_seconds=5,
    ):
        self.store, self.prepare, self.run, self.publish = store, prepare, run, publish
        self.clock = clock
        self.max_attempts = max(1, min(int(max_attempts), 3))
        self.timeout_seconds = min(max(float(timeout_seconds), 0.01), 60)
        self.lease_seconds = max(float(lease_seconds), self.timeout_seconds + 1)
        self.retry_seconds = max(float(retry_seconds), 0)
        self._lock = threading.Lock()
        self._unfinished = None
        self._blocked_since = None

    def health(self):
        busy = self._lock.locked()
        blocked = (
            not busy and self._unfinished is not None and self._unfinished.is_alive()
        )
        return {
            "status": "busy"
            if busy
            else "waiting_for_provider_shutdown"
            if blocked
            else "ready",
            "blocked_since": self._blocked_since if blocked else None,
        }

    def enqueue(self, db, meal, snapshot, evidence_revision):
        existing = db.execute(
            "SELECT * FROM generation_jobs WHERE meal_id=? AND context_revision=? AND evidence_revision=? AND policy_version=?",
            (meal["id"], meal["revision"], evidence_revision, policy_identity()),
        ).fetchone()
        if existing:
            if existing["status"] == "retry_wait":
                db.execute(
                    "UPDATE generation_jobs SET next_attempt_at=? WHERE id=?",
                    (self.clock(), existing["id"]),
                )
            return existing, False
        supersede_jobs(db, meal["id"], self.clock())
        job_id = uuid4().hex
        membership = db.execute(
            "SELECT membership_revision FROM rooms WHERE id=?", (meal["room_id"],)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO generation_jobs(id,meal_id,context_revision,evidence_revision,policy_version,membership_revision,max_attempts,created_at,input_fingerprint) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                meal["id"],
                meal["revision"],
                evidence_revision,
                policy_identity(),
                membership,
                self.max_attempts,
                timestamp(self.clock()),
                snapshot_identity(snapshot),
            ),
        )
        db.executemany(
            "INSERT INTO generation_inputs VALUES(?,?,?,?)",
            [
                (job_id, p["user_id"], p["profile_revision"], p["response_revision"])
                for p in snapshot["participants"]
            ],
        )
        return db.execute(
            "SELECT * FROM generation_jobs WHERE id=?", (job_id,)
        ).fetchone(), True

    def _finish(self, db, job, token, status, code=None, result=None, started=None):
        current = db.execute(
            "SELECT * FROM generation_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
            (job["id"], token, self.clock()),
        ).fetchone()
        if current is None:
            return False
        terminal = status != "retry_wait"
        finished = timestamp(self.clock())
        metadata = (
            result_metadata(result)
            if result is not None
            else {
                "input_tokens": None,
                "output_tokens": None,
                "usage_source": "unknown",
            }
        )
        db.execute(
            "UPDATE generation_attempts SET status=?,completed_at=?,error_code=?,duration_ms=?,metadata=? WHERE job_id=? AND number=? AND status='running'",
            (
                status,
                finished,
                code,
                round((time.monotonic() - started) * 1000)
                if started is not None
                else None,
                encode(metadata),
                job["id"],
                job["attempt_count"],
            ),
        )
        db.execute(
            "UPDATE generation_jobs SET status=?,error_code=?,completed_at=?,next_attempt_at=?,lease_token=NULL,lease_until=NULL WHERE id=?",
            (
                status,
                code,
                finished if terminal else None,
                self.clock()
                + self.retry_seconds * 2 ** max(0, job["attempt_count"] - 1),
                job["id"],
            ),
        )
        if terminal:
            db.execute(
                "UPDATE generation_jobs SET input_fingerprint=NULL WHERE id=?",
                (job["id"],),
            )
            db.execute("DELETE FROM generation_inputs WHERE job_id=?", (job["id"],))
        if status in {"failed", "superseded"}:
            db.execute(
                "UPDATE meals SET status='collecting' WHERE id=? AND revision=? AND status='generating'",
                (job["meal_id"], job["context_revision"]),
            )
        return True

    def tick(self):
        """Process at most one attempt. Safe to call again after a restart or lease loss."""
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            # Python cannot safely kill a stalled thread. Do not accumulate more
            # calls while the bounded provider invocation from a timed-out attempt unwinds.
            with self.store.transaction() as db:
                for expired in db.execute(
                    "SELECT * FROM generation_jobs WHERE status='running' AND lease_until<=?",
                    (self.clock(),),
                ).fetchall():
                    exhausted = expired["attempt_count"] >= expired["max_attempts"]
                    status = "failed" if exhausted else "retry_wait"
                    db.execute(
                        "UPDATE generation_attempts SET status='interrupted',completed_at=?,error_code='WORKER_INTERRUPTED' WHERE job_id=? AND number=? AND status='running'",
                        (
                            timestamp(self.clock()),
                            expired["id"],
                            expired["attempt_count"],
                        ),
                    )
                    db.execute(
                        "UPDATE generation_jobs SET status=?,error_code=?,completed_at=?,next_attempt_at=?,lease_token=NULL,lease_until=NULL WHERE id=?",
                        (
                            status,
                            "WORKER_INTERRUPTED",
                            timestamp(self.clock()) if exhausted else None,
                            self.clock(),
                            expired["id"],
                        ),
                    )
                    if exhausted:
                        db.execute(
                            "UPDATE generation_jobs SET input_fingerprint=NULL WHERE id=?",
                            (expired["id"],),
                        )
                        db.execute(
                            "DELETE FROM generation_inputs WHERE job_id=?",
                            (expired["id"],),
                        )
                        db.execute(
                            "UPDATE meals SET status='collecting' WHERE id=? AND revision=? AND status='generating'",
                            (expired["meal_id"], expired["context_revision"]),
                        )
                if self._unfinished and self._unfinished.is_alive():
                    return 0
                job = db.execute(
                    "SELECT * FROM generation_jobs WHERE status IN ('queued','retry_wait') AND next_attempt_at<=? ORDER BY created_at,id LIMIT 1",
                    (self.clock(),),
                ).fetchone()
                if job is None:
                    return 0
                token = uuid4().hex
                db.execute(
                    "UPDATE generation_jobs SET status='running',attempt_count=attempt_count+1,lease_token=?,lease_until=?,error_code=NULL WHERE id=?",
                    (token, self.clock() + self.lease_seconds, job["id"]),
                )
                job = db.execute(
                    "SELECT * FROM generation_jobs WHERE id=?", (job["id"],)
                ).fetchone()
                db.execute(
                    "INSERT INTO generation_attempts(job_id,number,status,started_at) VALUES(?,?,'running',?)",
                    (job["id"], job["attempt_count"], timestamp(self.clock())),
                )
                try:
                    snapshot = self.prepare(db, job)
                except Superseded:
                    self._finish(db, job, token, "superseded", "CONTEXT_CHANGED")
                    return 1
            started = time.monotonic()
            output, ready = {}, threading.Event()

            def execute():
                try:
                    output["result"] = self.run(snapshot)
                except Exception:  # noqa: BLE001 - provider errors must never escape into receipts.
                    output["failed"] = True
                finally:
                    ready.set()

            task = threading.Thread(
                target=execute, daemon=True, name="dining-generation"
            )
            self._unfinished = task
            task.start()
            timed_out = not ready.wait(self.timeout_seconds)
            self._blocked_since = timestamp(self.clock()) if timed_out else None
            with self.store.transaction() as db:
                current = db.execute(
                    "SELECT * FROM generation_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                    (job["id"], token, self.clock()),
                ).fetchone()
                if current is None:
                    return 1
                try:
                    self.prepare(
                        db, current
                    )  # Recheck authorization, context and live evidence.
                except Superseded:
                    self._finish(
                        db, job, token, "superseded", "CONTEXT_CHANGED", started=started
                    )
                    return 1
                if timed_out or output.get("failed"):
                    status = (
                        "retry_wait"
                        if job["attempt_count"] < job["max_attempts"]
                        else "failed"
                    )
                    self._finish(
                        db,
                        job,
                        token,
                        status,
                        "GENERATION_TIMEOUT" if timed_out else "PROVIDER_UNAVAILABLE",
                        started=started,
                    )
                else:
                    if self._finish(
                        db,
                        job,
                        token,
                        "published",
                        result=output["result"],
                        started=started,
                    ):
                        self.publish(db, job, output["result"])
            return 1
        finally:
            self._lock.release()
