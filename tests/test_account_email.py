"""Offline account-mail tests: fake transport, real token lifecycle and password hashing."""

import re
import time
from dataclasses import replace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from dining.accounts.email import AccountEmailService, EmailSettings, SmtpSender
from dining.core.store import DiningStore


class FakeSender:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send(self, recipient, subject, body, message_id):
        if self.fail:
            raise RuntimeError("SECRET SMTP ERROR recipient@example.test")
        self.messages.append((recipient, subject, body, message_id))


@pytest.fixture
def fixture(tmp_path):
    store = DiningStore(tmp_path / "accounts.sqlite3")
    hasher = PasswordHash.recommended()
    with store.transaction() as db:
        db.execute(
            "INSERT INTO users(id,email,name,password_hash,profile,created_at) VALUES(?,?,?,?,?,?)",
            (
                "u1",
                "owner@example.test",
                "Owner",
                hasher.hash("old-password-test"),
                "{}",
                "2026-10-05T00:00:00Z",
            ),
        )
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)",
            ("oldsession", "u1", "csrf", time.time() + 3600),
        )
    sender = FakeSender()
    settings = EmailSettings(
        host="smtp.example.test",
        sender="makan@example.test",
        public_base_url="https://makan.example.test",
        token_secret="test-only-secret-not-for-real-use-12345",
    )
    service = AccountEmailService(
        store, settings=settings, sender=sender, password_hasher=hasher
    )
    app = FastAPI()

    def authenticate(request: Request):
        if request.headers.get("x-test-auth") != "u1":
            raise HTTPException(401, "Sign in")
        if request.method == "POST" and request.headers.get("x-csrf-token") != "csrf":
            raise HTTPException(403, "CSRF")
        return {"user_id": "u1"}

    app.include_router(service.router(authenticate))
    yield store, service, sender, TestClient(app), hasher
    store.close()


def request_verification(client):
    return client.post(
        "/api/auth/email/request-verification",
        headers={"x-test-auth": "u1", "x-csrf-token": "csrf"},
        json={},
    )


def token_from(sender):
    return re.search(
        r"#(?:verify-email|reset-password)/([A-Za-z0-9_-]+)", sender.messages[-1][2]
    ).group(1)


def test_verification_is_queued_hashed_single_use_and_tied_to_current_email(fixture):
    store, service, sender, client, _ = fixture
    response = request_verification(client)
    assert response.status_code == 202
    assert sender.messages == []
    assert "owner@example.test" not in response.text
    assert service.tick()["sent"] == 1
    token = token_from(sender)
    with store.transaction() as db:
        dump = " ".join(db.iterdump())
    assert token not in dump and "#verify-email" not in dump
    assert client.post("/api/auth/email/verify", json={"token": token}).json() == {
        "verified": True
    }
    assert (
        client.post("/api/auth/email/verify", json={"token": token}).status_code == 400
    )
    assert service.is_verified("u1")
    with store.transaction() as db:
        db.execute("UPDATE users SET email='changed@example.test' WHERE id='u1'")
    assert not service.is_verified("u1")


def test_password_reset_changes_hash_revokes_sessions_and_never_signs_in(fixture):
    store, service, sender, client, hasher = fixture
    response = client.post(
        "/api/auth/password/request-reset", json={"email": "owner@example.test"}
    )
    assert response.status_code == 202
    service.tick()
    token = token_from(sender)
    response = client.post(
        "/api/auth/password/reset",
        json={"token": token, "password": "new-password-test"},
    )
    assert response.json() == {"reset": True}
    assert "set-cookie" not in response.headers
    with store.transaction() as db:
        assert hasher.verify(
            "new-password-test",
            db.execute("SELECT password_hash FROM users WHERE id='u1'").fetchone()[0],
        )
        assert (
            db.execute("SELECT COUNT(*) FROM sessions WHERE user_id='u1'").fetchone()[0]
            == 0
        )
    assert (
        client.post(
            "/api/auth/password/reset",
            json={"token": token, "password": "another-password"},
        ).status_code
        == 400
    )


def test_unknown_account_response_is_identical_and_delivery_is_not_attempted(fixture):
    _, service, sender, client, _ = fixture
    existing = client.post(
        "/api/auth/password/request-reset", json={"email": "owner@example.test"}
    )
    unknown = client.post(
        "/api/auth/password/request-reset", json={"email": "missing@example.test"}
    )
    assert existing.status_code == unknown.status_code == 202
    assert existing.json() == unknown.json()
    assert service.tick()["sent"] == 1
    assert len(sender.messages) == 1


def test_request_rate_limits_apply_to_unknown_addresses_and_survive_service_restart(
    fixture,
):
    store, service, sender, client, hasher = fixture
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/password/request-reset",
                json={"email": "missing@example.test"},
            ).status_code
            == 202
        )
    assert (
        client.post(
            "/api/auth/password/request-reset", json={"email": "missing@example.test"}
        ).status_code
        == 429
    )
    restarted = AccountEmailService(
        store, settings=service.settings, sender=sender, password_hasher=hasher
    )
    with pytest.raises(HTTPException) as error:
        restarted.request_reset("missing@example.test", "testclient")
    assert error.value.status_code == 429


def test_expired_or_changed_email_tokens_cannot_be_redeemed(fixture):
    store, service, sender, client, _ = fixture
    request_verification(client)
    service.tick()
    token = token_from(sender)
    with store.transaction() as db:
        db.execute("UPDATE account_email_tokens SET expires_at=0")
    assert (
        client.post("/api/auth/email/verify", json={"token": token}).status_code == 400
    )
    request_verification(client)
    service.tick()
    token = token_from(sender)
    with store.transaction() as db:
        db.execute("UPDATE users SET email='changed@example.test' WHERE id='u1'")
    assert (
        client.post("/api/auth/email/verify", json={"token": token}).status_code == 400
    )


def test_delivery_survives_restart_and_failure_is_redacted(fixture):
    store, service, sender, client, hasher = fixture
    request_verification(client)
    sender.fail = True
    assert service.tick()["failed"] == 1
    with store.transaction() as db:
        assert "SECRET SMTP ERROR" not in " ".join(db.iterdump())
        db.execute("UPDATE account_email_outbox SET next_attempt_at=0")
    sender.fail = False
    restarted = AccountEmailService(
        store, settings=service.settings, sender=sender, password_hasher=hasher
    )
    assert restarted.tick()["sent"] == 1
    assert (
        client.post(
            "/api/auth/email/verify", json={"token": token_from(sender)}
        ).status_code
        == 200
    )


def test_own_status_and_verification_request_require_auth_and_csrf(fixture):
    _, _, _, client, _ = fixture
    assert client.get("/api/auth/email/status").status_code == 401
    assert (
        client.post(
            "/api/auth/email/request-verification",
            headers={"x-test-auth": "u1"},
            json={},
        ).status_code
        == 403
    )
    status = client.get("/api/auth/email/status", headers={"x-test-auth": "u1"}).json()
    assert status == {
        "available": True,
        "verified": False,
        "verification_required": False,
    }


def test_public_endpoints_reject_cross_origin_and_redact_invalid_input(fixture):
    _, _, _, client, _ = fixture
    assert (
        client.post(
            "/api/auth/password/request-reset",
            headers={"origin": "https://attacker.test"},
            json={"email": "owner@example.test"},
        ).status_code
        == 403
    )
    response = client.post(
        "/api/auth/password/reset", json={"token": "SECRET_TOKEN", "password": "SECRET"}
    )
    assert response.status_code == 422
    assert "SECRET" not in response.text


@pytest.mark.parametrize(
    "base",
    [
        "http://public.example.test",
        "https://user:pass@example.test",
        "https://example.test/path",
        "https://example.test?token=secret",
        "javascript:alert(1)",
    ],
)
def test_email_configuration_rejects_untrusted_public_link_origins(base):
    with pytest.raises(ValueError):
        EmailSettings(
            host="smtp.example.test",
            sender="makan@example.test",
            public_base_url=base,
            token_secret="x" * 40,
        )


def test_required_verification_cannot_start_without_delivery_configuration():
    with pytest.raises(ValueError):
        EmailSettings.from_env({"DINING_REQUIRE_VERIFIED_EMAIL": "true"})
    settings = EmailSettings.from_env({})
    assert not settings.enabled and not settings.require_verified


def test_new_request_supersedes_previous_link(fixture):
    _, service, sender, client, _ = fixture
    request_verification(client)
    service.tick()
    old_token = token_from(sender)
    request_verification(client)
    service.tick()
    new_token = token_from(sender)
    assert old_token != new_token
    assert (
        client.post("/api/auth/email/verify", json={"token": old_token}).status_code
        == 400
    )
    assert (
        client.post("/api/auth/email/verify", json={"token": new_token}).status_code
        == 200
    )


def test_password_link_cannot_verify_email_and_verification_cannot_reset_password(
    fixture,
):
    _, service, sender, client, _ = fixture
    request_verification(client)
    service.tick()
    token = token_from(sender)
    assert (
        client.post(
            "/api/auth/password/reset",
            json={"token": token, "password": "new-password-test"},
        ).status_code
        == 400
    )
    assert (
        client.post("/api/auth/email/verify", json={"token": token}).status_code == 200
    )
    client.post(
        "/api/auth/password/request-reset", json={"email": "owner@example.test"}
    )
    service.tick()
    assert (
        client.post(
            "/api/auth/email/verify", json={"token": token_from(sender)}
        ).status_code
        == 400
    )


def test_changed_address_or_deleted_account_cancels_unsent_mail(fixture):
    store, service, sender, client, _ = fixture
    request_verification(client)
    with store.transaction() as db:
        db.execute("UPDATE users SET email='changed@example.test' WHERE id='u1'")
    assert service.tick()["sent"] == 0
    assert sender.messages == []
    with store.transaction() as db:
        assert (
            db.execute("SELECT nonce FROM account_email_outbox").fetchone()[0] is None
        )
        db.execute("DELETE FROM users WHERE id='u1'")
        for table in (
            "account_email_outbox",
            "account_email_tokens",
            "account_email_verified",
        ):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_retry_budget_and_expired_worker_lease_are_bounded(fixture):
    store, service, sender, client, _ = fixture
    request_verification(client)
    sender.fail = True
    for _ in range(3):
        assert service.tick()["failed"] == 1
        with store.transaction() as db:
            db.execute("UPDATE account_email_outbox SET next_attempt_at=0")
    assert service.tick()["failed"] == 0
    with store.transaction() as db:
        row = db.execute(
            "SELECT status,nonce,attempts FROM account_email_outbox"
        ).fetchone()
        assert tuple(row) == ("failed", None, 3)
        db.execute(
            "UPDATE account_email_outbox SET status='sending',lease_until=0,nonce='stale'"
        )
    assert service.tick()["sent"] == 0
    with store.transaction() as db:
        assert (
            db.execute("SELECT status FROM account_email_outbox").fetchone()[0]
            == "failed"
        )


def test_secret_rotation_invalidates_queued_links_instead_of_sending_them(fixture):
    store, service, sender, client, hasher = fixture
    request_verification(client)
    rotated = AccountEmailService(
        store,
        settings=replace(
            service.settings, token_secret="another-independent-test-secret-12345678"
        ),
        sender=sender,
        password_hasher=hasher,
    )
    assert rotated.tick() == {"sent": 0, "failed": 1}
    assert sender.messages == []


def test_optional_verified_gate_only_passes_verified_current_address(fixture):
    store, service, sender, client, hasher = fixture
    gated = AccountEmailService(
        store,
        settings=replace(service.settings, require_verified=True),
        sender=sender,
        password_hasher=hasher,
    )
    with pytest.raises(HTTPException) as error:
        gated.assert_verified("u1")
    assert error.value.status_code == 403
    request_verification(client)
    service.tick()
    client.post("/api/auth/email/verify", json={"token": token_from(sender)})
    gated.assert_verified("u1")


def test_smtp_sender_requires_tls_before_authentication_or_message(
    monkeypatch, fixture
):
    _, service, _, _, _ = fixture
    events = []

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            assert host == "smtp.example.test" and port == 587 and timeout == 10

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            events.append("ehlo")

        def starttls(self, context):
            assert context.check_hostname
            events.append("tls")

        def login(self, username, password):
            assert events[-1] == "ehlo" and "tls" in events
            events.append("auth")

        def send_message(self, message):
            assert events[-1] == "auth"
            events.append("sent")

    monkeypatch.setattr("dining.accounts.email.smtplib.SMTP", FakeSmtp)
    sender = SmtpSender(
        replace(service.settings, username="test-user", password="test-password")
    )
    sender.send(
        "owner@example.test", "Test", "Test message", "<test@makan.example.test>"
    )
    assert events == ["ehlo", "tls", "ehlo", "auth", "sent"]
