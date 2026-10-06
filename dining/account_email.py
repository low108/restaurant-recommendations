"""Optional account verification/recovery with durable, token-free email queues.

No mail is sent in a request handler. A configured worker calls ``tick``; tests
inject a fake sender. SMTP delivery is at-least-once, with bounded retries and a
stable Message-ID. Links use fragments so tokens do not enter HTTP access logs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from dining.store import DiningStore

GENERIC_REQUEST = {
    "accepted": True,
    "message": "If this account can receive the requested link, it will arrive shortly.",
}
EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


@dataclass(frozen=True)
class EmailSettings:
    host: str = ""
    sender: str = ""
    public_base_url: str = ""
    port: int = 587
    tls: str = "starttls"
    username: str = ""
    password: str = field(default="", repr=False)
    token_secret: str = field(default="", repr=False)
    require_verified: bool = False

    def __post_init__(self):
        if not self.host:
            if self.require_verified or self.sender:
                raise ValueError("Account email requires a complete SMTP configuration")
            return
        if not re.fullmatch(
            r"[a-zA-Z0-9.-]+", self.host
        ) or not EMAIL_PATTERN.fullmatch(self.sender):
            raise ValueError("Configure a valid SMTP host and sender address")
        if not 1 <= self.port <= 65535 or self.tls not in {"starttls", "ssl"}:
            raise ValueError("SMTP requires a valid port and starttls or ssl")
        if bool(self.username) != bool(self.password):
            raise ValueError("SMTP authentication requires both username and password")
        if len(self.token_secret.encode("utf-8")) < 32:
            raise ValueError(
                "DINING_EMAIL_TOKEN_SECRET must contain at least 32 bytes of independent random secret material"
            )
        origin = urlsplit(self.public_base_url)
        loopback = origin.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not origin.hostname
            or origin.username
            or origin.password
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
            or origin.scheme not in {"http", "https"}
            or (origin.scheme != "https" and not loopback)
            or any(character.isspace() for character in self.public_base_url)
        ):
            raise ValueError(
                "DINING_PUBLIC_BASE_URL must be an HTTPS origin (HTTP is limited to loopback development)"
            )
        try:
            _ = origin.port
        except ValueError as error:
            raise ValueError("DINING_PUBLIC_BASE_URL has an invalid port") from error

    @property
    def enabled(self) -> bool:
        return bool(self.host)

    @property
    def startup_state(self) -> str:
        if not self.host and not self.sender and not self.require_verified:
            return "disabled"
        if (
            self.host
            and self.sender
            and self.public_base_url
            and len(self.token_secret.encode("utf-8")) >= 32
            and (not self.username or self.password)
        ):
            return "configured"
        return "incomplete"

    def startup_status(self) -> dict:
        is_mock = (
            not self.host
            or "example" in self.host.lower()
            or "test" in self.host.lower()
        )
        return {
            "status": self.startup_state,
            "host": self.host or None,
            "port": self.port if self.host else None,
            "tls": self.tls if self.host else None,
            "sender_configured": bool(self.sender),
            "auth_configured": bool(self.username and self.password),
            "require_verified": self.require_verified,
            "delivery_mode": "mock" if is_mock else "live",
        }

    @classmethod
    def from_env(cls, env=None) -> EmailSettings:
        env = os.environ if env is None else env
        required = str(env.get("DINING_REQUIRE_VERIFIED_EMAIL", "false")).casefold()
        if required not in {"true", "false", "1", "0"}:
            raise ValueError("DINING_REQUIRE_VERIFIED_EMAIL must be true or false")
        return cls(
            host=env.get("DINING_SMTP_HOST", ""),
            sender=env.get("DINING_SMTP_FROM", ""),
            public_base_url=env.get("DINING_PUBLIC_BASE_URL", ""),
            port=int(env.get("DINING_SMTP_PORT", "587")),
            tls=env.get("DINING_SMTP_TLS", "starttls"),
            username=env.get("DINING_SMTP_USERNAME", ""),
            password=env.get("DINING_SMTP_PASSWORD", ""),
            token_secret=env.get("DINING_EMAIL_TOKEN_SECRET", ""),
            require_verified=required in {"true", "1"},
        )


class SmtpSender:
    def __init__(self, settings: EmailSettings):
        self.settings = settings

    def send(self, recipient: str, subject: str, body: str, message_id: str) -> None:
        settings = self.settings
        message = EmailMessage()
        message["From"], message["To"] = settings.sender, recipient
        message["Subject"], message["Message-ID"] = subject, message_id
        message.set_content(body)
        context = ssl.create_default_context()
        if settings.tls == "ssl":
            connection = smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=10, context=context
            )
        else:
            connection = smtplib.SMTP(settings.host, settings.port, timeout=10)
        with connection as smtp:
            smtp.ehlo()
            if settings.tls == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if settings.username:
                smtp.login(settings.username, settings.password)
            smtp.send_message(message)


class AccountEmailService:
    def __init__(
        self,
        store: DiningStore,
        *,
        settings: EmailSettings | None = None,
        sender=None,
        password_hasher=None,
    ):
        self.store = store
        self.settings = settings if settings is not None else EmailSettings.from_env()
        self.sender = sender if sender is not None else SmtpSender(self.settings)
        self.password_hasher = password_hasher or PasswordHash.recommended()
        with store.transaction() as db:
            # Separate additive tables leave the existing account schema untouched.
            for statement in (
                "CREATE TABLE IF NOT EXISTS account_email_verified(user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,email TEXT NOT NULL,verified_at REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS account_email_tokens(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,email TEXT NOT NULL,purpose TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,consumed_at REAL)",
                "CREATE TABLE IF NOT EXISTS account_email_outbox(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,token_hash TEXT NOT NULL REFERENCES account_email_tokens(token_hash) ON DELETE CASCADE,email TEXT NOT NULL,purpose TEXT NOT NULL,nonce TEXT,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at REAL NOT NULL,lease_until REAL,expires_at REAL NOT NULL,last_failure TEXT,created_at REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS account_email_requests(bucket TEXT NOT NULL,created_at REAL NOT NULL)",
                "CREATE INDEX IF NOT EXISTS account_email_request_bucket ON account_email_requests(bucket,created_at)",
            ):
                db.execute(statement)

    def _enabled(self):
        if not self.settings.enabled:
            raise HTTPException(503, "Email delivery is not configured on this server")

    def _hash(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _keyed(self, value: str) -> str:
        return hmac.new(
            self.settings.token_secret.encode("utf-8"),
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _token(self, nonce: str, purpose: str, user_id: str, email: str) -> str:
        raw = hmac.new(
            self.settings.token_secret.encode("utf-8"),
            f"account-link:{nonce}:{purpose}:{user_id}:{email}".encode(),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def _limit(self, ip: str, *, email: str | None = None):
        current = time.time()
        buckets = [(self._keyed("ip:" + ip), 40)]
        if email is not None:
            buckets.append((self._keyed("email:" + email), 5))
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM account_email_requests WHERE created_at<?",
                (current - 900,),
            )
            if any(
                db.execute(
                    "SELECT COUNT(*) FROM account_email_requests WHERE bucket=?",
                    (bucket,),
                ).fetchone()[0]
                >= maximum
                for bucket, maximum in buckets
            ):
                raise HTTPException(429, "Too many requests. Try again in 15 minutes")
            db.executemany(
                "INSERT INTO account_email_requests VALUES(?,?)",
                [(bucket, current) for bucket, _ in buckets],
            )

    def is_verified(self, user_id: str) -> bool:
        with self.store.transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM account_email_verified v JOIN users u ON u.id=v.user_id AND u.email=v.email WHERE u.id=?",
                    (user_id,),
                ).fetchone()
                is not None
            )

    def assert_verified(self, user_id: str) -> None:
        if self.settings.require_verified and not self.is_verified(user_id):
            raise HTTPException(
                403, "Verify your email before using shared dining features"
            )

    def enqueue_verification(self, user_id: str, email: str) -> None:
        self._queue(user_id, email, "verify")

    def enqueue_recovery(self, user_id: str, email: str) -> None:
        self._queue(user_id, email, "reset")

    def _queue(self, user_id: str, email: str, purpose: str):
        current, nonce = time.time(), secrets.token_urlsafe(32)
        lifetime = 86400 if purpose == "verify" else 1800
        token_hash = self._hash(self._token(nonce, purpose, user_id, email))
        with self.store.transaction() as db:
            user = db.execute(
                "SELECT email FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if not user or user["email"] != email:
                return
            db.execute(
                "UPDATE account_email_tokens SET consumed_at=? WHERE user_id=? AND purpose=? AND consumed_at IS NULL",
                (current, user_id, purpose),
            )
            db.execute(
                "UPDATE account_email_outbox SET status='superseded',nonce=NULL WHERE user_id=? AND purpose=? AND status IN ('pending','sending')",
                (user_id, purpose),
            )
            db.execute(
                "INSERT INTO account_email_tokens VALUES(?,?,?,?,?,?,NULL)",
                (token_hash, user_id, email, purpose, current, current + lifetime),
            )
            db.execute(
                "INSERT INTO account_email_outbox(id,user_id,token_hash,email,purpose,nonce,status,next_attempt_at,expires_at,created_at) VALUES(?,?,?,?,?,?,'pending',?,?,?)",
                (
                    str(uuid.uuid4()),
                    user_id,
                    token_hash,
                    email,
                    purpose,
                    nonce,
                    current,
                    current + lifetime,
                    current,
                ),
            )

    def request_reset(self, email: str, ip: str) -> None:
        self._enabled()
        email = (
            email.lower().strip()
        )  # Match the account API's canonicalization exactly.
        self._limit(ip, email=email)
        with self.store.transaction() as db:
            user = db.execute(
                "SELECT id,email FROM users WHERE email=?", (email,)
            ).fetchone()
        if user:
            self._queue(user["id"], user["email"], "reset")

    def request_verification(self, user_id: str, ip: str) -> None:
        self._enabled()
        with self.store.transaction() as db:
            user = db.execute(
                "SELECT email FROM users WHERE id=?", (user_id,)
            ).fetchone()
        if not user:
            raise HTTPException(401, "Sign in to continue")
        self._limit(ip, email=user["email"])
        if not self.is_verified(user_id):
            self._queue(user_id, user["email"], "verify")

    def _valid_token(self, db, token: str, purpose: str):
        row = db.execute(
            "SELECT t.* FROM account_email_tokens t JOIN users u ON u.id=t.user_id AND u.email=t.email WHERE t.token_hash=? AND t.purpose=? AND t.consumed_at IS NULL AND t.expires_at>?",
            (self._hash(token), purpose, time.time()),
        ).fetchone()
        if not row:
            raise HTTPException(
                400, "This link is invalid or expired. Request a new link"
            )
        return row

    def verify(self, token: str, ip: str) -> None:
        self._enabled()
        self._limit(ip)
        with self.store.transaction() as db:
            row = self._valid_token(db, token, "verify")
            db.execute(
                "INSERT INTO account_email_verified VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET email=excluded.email,verified_at=excluded.verified_at",
                (row["user_id"], row["email"], time.time()),
            )
            db.execute(
                "UPDATE account_email_tokens SET consumed_at=? WHERE token_hash=?",
                (time.time(), row["token_hash"]),
            )

    def reset_password(self, token: str, password: str, ip: str) -> None:
        self._enabled()
        self._limit(ip)
        # Check validity before doing expensive hashing, then check again atomically.
        with self.store.transaction() as db:
            self._valid_token(db, token, "reset")
        password_hash = self.password_hasher.hash(password)
        with self.store.transaction() as db:
            row = self._valid_token(db, token, "reset")
            db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (password_hash, row["user_id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
            db.execute(
                "UPDATE account_email_tokens SET consumed_at=? WHERE user_id=? AND purpose='reset' AND consumed_at IS NULL",
                (time.time(), row["user_id"]),
            )
            db.execute(
                "UPDATE account_email_outbox SET status='superseded',nonce=NULL WHERE user_id=? AND purpose='reset' AND status IN ('pending','sending')",
                (row["user_id"],),
            )

    def tick(self, *, limit: int = 5) -> dict:
        result = {"sent": 0, "failed": 0}
        if not self.settings.enabled:
            return result
        for _ in range(min(max(limit, 0), 5)):
            current = time.time()
            with self.store.transaction() as db:
                db.execute(
                    "DELETE FROM account_email_tokens WHERE expires_at<?",
                    (current - 7 * 86400,),
                )
                db.execute(
                    "UPDATE account_email_outbox SET status='failed',nonce=NULL,last_failure='delivery_failed',lease_until=NULL WHERE status='sending' AND attempts>=3 AND lease_until<?",
                    (current,),
                )
                db.execute(
                    "UPDATE account_email_outbox SET status='expired',nonce=NULL WHERE status IN ('pending','sending') AND (expires_at<=? OR NOT EXISTS(SELECT 1 FROM users u JOIN account_email_tokens t ON t.user_id=u.id WHERE u.id=account_email_outbox.user_id AND u.email=account_email_outbox.email AND t.token_hash=account_email_outbox.token_hash AND t.consumed_at IS NULL))",
                    (current,),
                )
                row = db.execute(
                    "SELECT * FROM account_email_outbox WHERE nonce IS NOT NULL AND next_attempt_at<=? AND attempts<3 AND (status='pending' OR (status='sending' AND lease_until<?)) ORDER BY created_at LIMIT 1",
                    (current, current),
                ).fetchone()
                if row is None:
                    break
                db.execute(
                    "UPDATE account_email_outbox SET status='sending',attempts=attempts+1,lease_until=? WHERE id=?",
                    (current + 60, row["id"]),
                )
            route = "verify-email" if row["purpose"] == "verify" else "reset-password"
            action = (
                "Verify your email"
                if row["purpose"] == "verify"
                else "Reset your password"
            )
            token = self._token(
                row["nonce"], row["purpose"], row["user_id"], row["email"]
            )
            # Secret rotation invalidates unsent messages instead of sending unusable links.
            valid = hmac.compare_digest(self._hash(token), row["token_hash"])
            failed = not valid
            if valid:
                link = f"{self.settings.public_base_url.rstrip('/')}/#{route}/{token}"
                body = f"{action} for Makan Together.\n\n{link}\n\nThis single-use link expires. If you did not request it, ignore this message."
                try:
                    domain = urlsplit(self.settings.public_base_url).hostname
                    self.sender.send(
                        row["email"],
                        f"Makan Together — {action}",
                        body,
                        f"<{row['id']}@{domain}>",
                    )
                except Exception:  # noqa: BLE001 - SMTP errors can contain addresses, credentials or links.
                    failed = True
            with self.store.transaction() as db:
                if failed:
                    terminal = row["attempts"] + 1 >= 3 or not valid
                    db.execute(
                        "UPDATE account_email_outbox SET status=?,nonce=CASE WHEN ? THEN NULL ELSE nonce END,last_failure='delivery_failed',next_attempt_at=?,lease_until=NULL WHERE id=? AND status='sending'",
                        (
                            "failed" if terminal else "pending",
                            terminal,
                            current + 60 * (2 ** row["attempts"]),
                            row["id"],
                        ),
                    )
                    result["failed"] += 1
                else:
                    db.execute(
                        "UPDATE account_email_outbox SET status='sent',nonce=NULL,last_failure=NULL,lease_until=NULL WHERE id=? AND status='sending'",
                        (row["id"],),
                    )
                    result["sent"] += 1
        return result

    def delivery_health(self) -> dict:
        with self.store.transaction() as db:
            total = db.execute("SELECT COUNT(*) FROM account_email_outbox").fetchone()[
                0
            ]
            sent = db.execute(
                "SELECT COUNT(*) FROM account_email_outbox WHERE status='sent'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM account_email_outbox WHERE status='pending'"
            ).fetchone()[0]
            failed = db.execute(
                "SELECT COUNT(*) FROM account_email_outbox WHERE status='failed'"
            ).fetchone()[0]
            retrying = db.execute(
                "SELECT COUNT(*) FROM account_email_outbox WHERE status='pending' AND attempts > 0"
            ).fetchone()[0]
            last_fail_row = db.execute(
                "SELECT last_failure FROM account_email_outbox WHERE last_failure IS NOT NULL ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            last_failure = last_fail_row[0] if last_fail_row else None
            is_mock = (
                not self.settings.host
                or "example" in self.settings.host.lower()
                or "test" in self.settings.host.lower()
            )
            return {
                "status": self.settings.startup_state,
                "total_jobs": total,
                "sent_count": sent,
                "pending_count": pending,
                "retrying_count": retrying,
                "failed_count": failed,
                "last_failure": last_failure,
                "delivery_mode": "mock" if is_mock else "live",
            }

    def router(self, authenticate) -> APIRouter:
        router = APIRouter(prefix="/api/auth")
        auth_dependency = Depends(authenticate)

        def ip(request):
            return request.client.host if request.client else "unknown"

        async def payload(request: Request, fields: set[str]) -> dict:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "This request must come from this application")
            if (
                not request.headers.get("content-type", "")
                .lower()
                .startswith("application/json")
            ):
                raise HTTPException(415, "Send JSON")
            try:
                body = await request.json()
            except (ValueError, UnicodeError) as error:
                raise HTTPException(422, "Provide valid request fields") from error
            if (
                not isinstance(body, dict)
                or set(body) != fields
                or any(not isinstance(value, str) for value in body.values())
            ):
                raise HTTPException(422, "Provide valid request fields")
            return body

        def valid_token(token):
            if not TOKEN_PATTERN.fullmatch(token):
                raise HTTPException(422, "Provide a complete link token")

        @router.get("/email/status")
        def status(auth=auth_dependency):
            return {
                "available": self.settings.enabled,
                "verified": self.is_verified(auth["user_id"]),
                "verification_required": self.settings.require_verified,
            }

        @router.post("/email/request-verification", status_code=202)
        async def request_verification(request: Request, auth=auth_dependency):
            await payload(request, set())
            await run_in_threadpool(
                self.request_verification, auth["user_id"], ip(request)
            )
            return GENERIC_REQUEST

        @router.post("/email/verify")
        async def verify(request: Request):
            body = await payload(request, {"token"})
            valid_token(body["token"])
            await run_in_threadpool(self.verify, body["token"], ip(request))
            return {"verified": True}

        @router.post("/password/request-reset", status_code=202)
        async def request_reset(request: Request):
            body = await payload(request, {"email"})
            email = body["email"].strip().lower()
            if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
                raise HTTPException(422, "Enter a valid email address")
            await run_in_threadpool(self.request_reset, email, ip(request))
            return GENERIC_REQUEST

        @router.post("/password/reset")
        async def reset(request: Request):
            body = await payload(request, {"token", "password"})
            valid_token(body["token"])
            password = body["password"].strip()
            if not 10 <= len(password) <= 128:
                raise HTTPException(422, "Use a password between 10 and 128 characters")
            await run_in_threadpool(
                self.reset_password, body["token"], password, ip(request)
            )
            return {"reset": True}

        return router
