"""Create disposable browser fixtures through the actual local HTTP API.

Run against a separate --demo server and an empty test database. Never use a
real account or imported catalog. The fixed password belongs only to test users.
"""

import argparse
import json
from urllib.parse import urlsplit

import httpx


def seed(base_url, namespace=""):
    if namespace and (
        len(namespace) > 40 or not all(c.isalnum() or c == "-" for c in namespace)
    ):
        raise ValueError(
            "Fixture namespace must contain only letters, digits and hyphens"
        )
    prefix = namespace + "-" if namespace else ""
    parsed = urlsplit(base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Browser fixtures require a loopback test server")
    health = httpx.get(base_url + "/health").json()
    if health.get("demo") is not True:
        raise ValueError("Refusing to seed a non-demo server")
    clients = []
    for number in range(1, 10):
        client = httpx.Client(base_url=base_url, timeout=30)
        result = client.post(
            "/api/auth/register",
            json={
                "name": f"E2E Diner {number}",
                "email": f"{prefix}e2e{number}@example.test",
                "password": "local-e2e-only-709",
                "adult_confirmed": True,
                "terms_accepted": True,
            },
        )
        result.raise_for_status()
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        profile = client.patch(
            "/api/profile",
            json={
                "allergy_status": "none",
                "halal_policy": "none",
                "requirements_reviewed": True,
                "max_budget": 50,
            },
        )
        profile.raise_for_status()
        clients.append(client)
    room = clients[0].post("/api/rooms", json={"name": "Nine-person E2E table"})
    room.raise_for_status()
    data = room.json()
    for client in clients[1:]:
        client.post(
            "/api/rooms/join", json={"token": data["invite_token"]}
        ).raise_for_status()
    for client in clients:
        client.close()
    return {
        "room_id": data["room"]["id"],
        "members": 9,
        "owner_email": f"{prefix}e2e1@example.test",
        "friend_email": f"{prefix}e2e2@example.test",
        "test_password": "local-e2e-only-709",
        "synthetic": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:7863")
    args = parser.parse_args()
    print(json.dumps(seed(args.base_url), indent=2))
