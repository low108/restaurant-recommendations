from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from test_api import make_client

from webapp import create_app


def test_configured_verification_blocks_rooms_but_preserves_account_rights(
    tmp_path, monkeypatch
):
    for key, value in {
        "DINING_SMTP_HOST": "smtp.example.test",
        "DINING_SMTP_FROM": "test@example.test",
        "DINING_PUBLIC_BASE_URL": "http://localhost:7863",
        "DINING_EMAIL_TOKEN_SECRET": "fictional-e2e-secret-709-709-709-709",
        "DINING_REQUIRE_VERIFIED_EMAIL": "true",
    }.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        async_generation=False,
        db_path=tmp_path / "verification.sqlite3",
        demo_mode=True,
    )
    delivered = []

    class TestMailbox:
        def send(self, recipient, subject, body, message_id):
            delivered.append(body)

    app.state.account_email.sender = TestMailbox()
    person = make_client(app, "EmailJourney")
    assert person.get("/api/profile").status_code == 200
    assert person.get("/api/export").status_code == 200
    assert (
        person.post("/api/rooms", json={"name": "Not yet verified"}).status_code == 403
    )
    assert (
        person.post("/api/auth/email/request-verification", json={}).status_code == 202
    )
    assert app.state.account_email.tick()["sent"] == 1
    link = next(word for word in delivered[0].split() if word.startswith("http"))
    token = urlsplit(link).fragment.split("/")[-1]
    assert (
        person.post("/api/auth/email/verify", json={"token": token}).status_code == 200
    )
    assert (
        person.post("/api/auth/email/verify", json={"token": token}).status_code == 400
    )
    assert person.get("/api/auth/email/status").json()["verified"] is True
    assert (
        person.post("/api/rooms", json={"name": "Verified test room"}).status_code
        == 201
    )
    anonymous = TestClient(app)
    assert anonymous.get("/api/auth/email/status").status_code == 401
    app.state.store.close()
