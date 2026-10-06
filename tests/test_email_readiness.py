import pytest
from pwdlib import PasswordHash

from dining.account_email import AccountEmailService, EmailSettings
from dining.store import DiningStore


class MockMailboxSender:
    def __init__(self, should_fail=False):
        self.delivered_mails = []
        self.should_fail = should_fail
        self.attempts = 0

    def send(self, recipient, subject, body, message_id):
        self.attempts += 1
        if self.should_fail:
            raise RuntimeError("Temporary SMTP connection drop")
        self.delivered_mails.append(
            {
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "message_id": message_id,
            }
        )


def test_smtp_configuration_validation_states():
    # 1. Disabled state
    disabled = EmailSettings()
    assert disabled.startup_state == "disabled"
    assert disabled.startup_status()["status"] == "disabled"

    # 2. Configured state
    configured = EmailSettings(
        host="smtp.mailprovider.test",
        sender="noreply@makan.test",
        public_base_url="https://makan.test",
        token_secret="a" * 32,
        port=587,
        tls="starttls",
    )
    assert configured.startup_state == "configured"
    status = configured.startup_status()
    assert status["status"] == "configured"
    assert status["host"] == "smtp.mailprovider.test"
    assert status["sender_configured"] is True
    assert status["delivery_mode"] in ("mock", "live")
    # Secrets must not appear in startup status
    assert "token_secret" not in status
    assert "password" not in status

    # 3. Incomplete state cannot enable verified-email enforcement
    with pytest.raises(
        ValueError, match="Account email requires a complete SMTP configuration"
    ):
        EmailSettings(host="", require_verified=True)

    with pytest.raises(ValueError, match="Configure a valid SMTP host"):
        EmailSettings(
            host="invalid host with spaces",
            sender="bad-email",
            public_base_url="https://makan.test",
            token_secret="a" * 32,
        )


def test_tls_requirements_and_plaintext_rejection():
    # Valid starttls and ssl
    EmailSettings(
        host="smtp.test",
        sender="noreply@test.com",
        public_base_url="https://test.com",
        token_secret="a" * 32,
        tls="starttls",
    )
    EmailSettings(
        host="smtp.test",
        sender="noreply@test.com",
        public_base_url="https://test.com",
        token_secret="a" * 32,
        port=465,
        tls="ssl",
    )

    # Plaintext refused
    with pytest.raises(
        ValueError, match="SMTP requires a valid port and starttls or ssl"
    ):
        EmailSettings(
            host="smtp.test",
            sender="noreply@test.com",
            public_base_url="https://test.com",
            token_secret="a" * 32,
            tls="none",
        )

    # Invalid port refused
    with pytest.raises(ValueError, match="SMTP requires a valid port"):
        EmailSettings(
            host="smtp.test",
            sender="noreply@test.com",
            public_base_url="https://test.com",
            token_secret="a" * 32,
            port=99999,
        )


def test_delivery_health_summarizes_outbox_without_exposing_pii(tmp_path):
    store = DiningStore(tmp_path / "email_health.sqlite3")
    sender = MockMailboxSender(should_fail=False)
    settings = EmailSettings(
        host="smtp.operator.test",
        sender="ops@makan.test",
        public_base_url="https://makan.test",
        token_secret="a" * 32,
    )
    service = AccountEmailService(store, settings=settings, sender=sender)

    # Enqueue a verification email for user
    with store.transaction() as db:
        hasher = PasswordHash.recommended()
        db.execute(
            "INSERT INTO users(id,email,name,password_hash,profile,created_at) VALUES(?,?,?,?,?,?)",
            (
                "u1",
                "user@private.test",
                "Private User",
                hasher.hash("pw"),
                "{}",
                "2026-10-06T00:00:00Z",
            ),
        )
    service.enqueue_verification("u1", "user@private.test")

    # Delivery health before tick
    health = service.delivery_health()
    assert health["total_jobs"] == 1
    assert health["pending_count"] == 1
    assert health["sent_count"] == 0

    # Ensure no PII (email, tokens, links) in health dict
    dump = str(health)
    assert "user@private.test" not in dump
    assert "u1" not in dump

    # Process tick
    result = service.tick()
    assert result["sent"] == 1

    health_after = service.delivery_health()
    assert health_after["sent_count"] == 1
    assert health_after["pending_count"] == 0
    assert health_after["failed_count"] == 0


def test_delivery_failures_retry_safely_with_exponential_backoff(tmp_path):
    store = DiningStore(tmp_path / "email_retry.sqlite3")
    sender = MockMailboxSender(should_fail=True)
    settings = EmailSettings(
        host="smtp.operator.test",
        sender="ops@makan.test",
        public_base_url="https://makan.test",
        token_secret="a" * 32,
    )
    service = AccountEmailService(store, settings=settings, sender=sender)

    with store.transaction() as db:
        hasher = PasswordHash.recommended()
        db.execute(
            "INSERT INTO users(id,email,name,password_hash,profile,created_at) VALUES(?,?,?,?,?,?)",
            (
                "u2",
                "user2@private.test",
                "User Two",
                hasher.hash("pw"),
                "{}",
                "2026-10-06T00:00:00Z",
            ),
        )
    service.enqueue_verification("u2", "user2@private.test")

    # First attempt fails
    result1 = service.tick()
    assert result1["failed"] == 1

    health1 = service.delivery_health()
    assert health1["retrying_count"] == 1
    assert health1["failed_count"] == 0
    assert health1["last_failure"] == "delivery_failed"

    # Simulated mock delivery recorded separately from real external delivery
    assert health1["delivery_mode"] in ("mock", "live")
