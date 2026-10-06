"""Durable web push delivery pipeline.

Prevents duplicate notifications across process restarts, suppresses expired
reminders, handles provider uncertainty (ambiguous acks), and disables 410 Gone subscriptions.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from dining.store import DiningStore, encode


class FakePushGateway:
    def __init__(
        self,
        *,
        simulate_ambiguous: bool = False,
        simulate_status_code: int = 201,
    ):
        self.simulate_ambiguous = simulate_ambiguous
        self.simulate_status_code = simulate_status_code
        self.delivered_count = 0
        self.calls = []

    def send_push(
        self,
        endpoint: str,
        p256dh: str,
        auth: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((endpoint, payload))
        if self.simulate_ambiguous:
            raise TimeoutError("Ambiguous gateway response / timeout")

        if self.simulate_status_code in (404, 410):
            return {"status_code": self.simulate_status_code, "ok": False}

        if self.simulate_status_code >= 400:
            return {"status_code": self.simulate_status_code, "ok": False}

        self.delivered_count += 1
        return {"status_code": self.simulate_status_code, "ok": True}


class PushDeliveryWorker:
    def __init__(
        self,
        store: DiningStore,
        gateway: Any = None,
        lease_seconds: int = 30,
    ):
        self.store = store
        self.gateway = gateway or FakePushGateway()
        self.lease_seconds = lease_seconds

    def enqueue_job(
        self,
        user_id: str,
        subscription_id: str,
        event_id: str,
        kind: str,
        payload: dict[str, Any],
        expires_at: float,
    ) -> bool:
        job_id = str(uuid.uuid4())
        now = time.time()
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

        with self.store.transaction() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO push_delivery_jobs (
                    id, user_id, subscription_id, event_id, kind, payload,
                    status, attempts, max_attempts, next_attempt_at,
                    lease_token, lease_until, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, 3, 0, NULL, NULL, ?, ?)
                """,
                (
                    job_id,
                    user_id,
                    subscription_id,
                    event_id,
                    kind,
                    encode(payload),
                    expires_at,
                    created_at,
                ),
            )
            return cursor.rowcount > 0

    def tick(self) -> dict[str, int]:
        current = time.time()
        stats = {
            "delivered": 0,
            "expired": 0,
            "failed": 0,
            "ambiguous": 0,
            "deferred": 0,
        }

        with self.store.transaction() as db:
            # 1. Suppress and mark expired jobs
            db.execute(
                """
                UPDATE push_delivery_jobs
                SET status='expired'
                WHERE status IN ('pending', 'retry_wait') AND expires_at <= ?
                """,
                (current,),
            )
            expired_count = db.execute("SELECT changes()").fetchone()[0]
            stats["expired"] = expired_count

            # 2. Reclaim expired leases
            db.execute(
                """
                UPDATE push_delivery_jobs
                SET status='pending', lease_token=NULL, lease_until=NULL
                WHERE status='sending' AND lease_until <= ?
                """,
                (current,),
            )

            # 3. Claim pending jobs
            jobs = db.execute(
                """
                SELECT j.*, s.endpoint, s.p256dh, s.auth, s.status as sub_status
                FROM push_delivery_jobs j
                JOIN push_subscriptions s ON s.id = j.subscription_id
                WHERE j.status='pending' AND j.next_attempt_at <= ? AND j.expires_at > ?
                LIMIT 20
                """,
                (current, current),
            ).fetchall()

            for job in jobs:
                lease_token = str(uuid.uuid4())
                lease_until = current + self.lease_seconds
                db.execute(
                    """
                    UPDATE push_delivery_jobs
                    SET status='sending', lease_token=?, lease_until=?, attempts=attempts+1
                    WHERE id=? AND status='pending'
                    """,
                    (lease_token, lease_until, job["id"]),
                )

                endpoint = job["endpoint"]
                p256dh = job["p256dh"]
                auth = job["auth"]
                payload = json.loads(job["payload"])

                try:
                    res = self.gateway.send_push(
                        endpoint=endpoint,
                        p256dh=p256dh,
                        auth=auth,
                        payload=payload,
                    )
                    status_code = res.get("status_code", 200)

                    if res.get("ok"):
                        db.execute(
                            """
                            UPDATE push_delivery_jobs
                            SET status='delivered', acknowledged_at=?, lease_token=NULL, lease_until=NULL
                            WHERE id=?
                            """,
                            (time.time(), job["id"]),
                        )
                        stats["delivered"] += 1
                    elif status_code in (404, 410):
                        # Subscription expired or invalid: disable subscription
                        db.execute(
                            "UPDATE push_subscriptions SET status='disabled' WHERE id=?",
                            (job["subscription_id"],),
                        )
                        db.execute(
                            """
                            UPDATE push_delivery_jobs
                            SET status='failed', last_error='subscription_gone', lease_token=NULL, lease_until=NULL
                            WHERE id=?
                            """,
                            (job["id"],),
                        )
                        stats["failed"] += 1
                    else:
                        db.execute(
                            """
                            UPDATE push_delivery_jobs
                            SET status='failed', last_error=?, lease_token=NULL, lease_until=NULL
                            WHERE id=?
                            """,
                            (f"http_{status_code}", job["id"]),
                        )
                        stats["failed"] += 1
                except TimeoutError:
                    # Ambiguous ack / provider uncertainty
                    db.execute(
                        """
                        UPDATE push_delivery_jobs
                        SET status='ambiguous', last_error='provider_timeout', lease_token=NULL, lease_until=NULL
                        WHERE id=?
                        """,
                        (job["id"],),
                    )
                    stats["ambiguous"] += 1
                except Exception:  # noqa: BLE001 - push delivery exceptions should not crash the worker loop
                    db.execute(
                        """
                        UPDATE push_delivery_jobs
                        SET status='failed', last_error='delivery_exception', lease_token=NULL, lease_until=NULL
                        WHERE id=?
                        """,
                        (job["id"],),
                    )
                    stats["failed"] += 1

        return stats

    def health(self) -> dict[str, Any]:
        with self.store.transaction() as db:
            total = db.execute("SELECT COUNT(*) FROM push_delivery_jobs").fetchone()[0]
            delivered = db.execute(
                "SELECT COUNT(*) FROM push_delivery_jobs WHERE status='delivered'"
            ).fetchone()[0]
            expired = db.execute(
                "SELECT COUNT(*) FROM push_delivery_jobs WHERE status='expired'"
            ).fetchone()[0]
            failed = db.execute(
                "SELECT COUNT(*) FROM push_delivery_jobs WHERE status='failed'"
            ).fetchone()[0]
            ambiguous = db.execute(
                "SELECT COUNT(*) FROM push_delivery_jobs WHERE status='ambiguous'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM push_delivery_jobs WHERE status='pending'"
            ).fetchone()[0]
            return {
                "total_jobs": total,
                "delivered_count": delivered,
                "pending_count": pending,
                "expired_count": expired,
                "failed_count": failed,
                "ambiguous_count": ambiguous,
            }
