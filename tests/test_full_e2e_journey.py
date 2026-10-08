"""Comprehensive end-to-end lifecycle verification.

Covers the full user and table journey:
Registration -> Profile -> Table creation -> Pre-join preview -> Join ->
Overlapping schedule conflict warning -> Check-in with must-leave-by ->
Adaptive question -> Shortlist generation -> Recommendation archive persistence ->
Data-error reporting boundary -> Private veto & resolution -> Selection modes ->
Post-meal transition (awaiting_feedback) -> Inbox notification -> Rich feedback &
alternate branch linkage -> Venue proposal learning -> Session close (7 days) ->
Export -> Deletion cascade.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from test_recommendation import ready_catalog

from dining.catalog.models import Catalog
from dining.notifications.reminders import NotificationService
from webapp import create_app


def make_client(app, name, email):
    client = TestClient(app)
    res = client.post(
        "/api/auth/register",
        json={
            "name": name,
            "email": email,
            "password": "long-e2e-password-123",
            "adult_confirmed": True,
            "terms_accepted": True,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    client.user_id = data["user"]["id"]
    return client


def test_complete_end_to_end_group_dining_journey(tmp_path, monkeypatch):
    # 1. Setup app and synthetic catalog
    catalog_path = tmp_path / "catalog.json"
    cat = ready_catalog().model_dump(mode="json")
    for s in cat["sources"]:
        s["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    for o in cat["outlets"]:
        o["opening_exceptions_coverage"]["ends_on"] = (
            (datetime.now(timezone.utc) + timedelta(days=365)).date().isoformat()
        )
    catalog_path.write_text(Catalog.model_validate(cat).model_dump_json())

    db_path = tmp_path / "journey.sqlite3"
    app = create_app(
        async_generation=False,
        db_path=db_path,
        catalog_path=catalog_path,
        demo_mode=True,
    )
    store = app.state.store
    notif_svc = NotificationService(store)

    # 2. Register participants: Host, GuestA, GuestB, Outsider
    host = make_client(app, "Host Alice", "alice@example.test")
    guest_a = make_client(app, "Guest Bob", "bob@example.test")
    guest_b = make_client(app, "Guest Charlie", "charlie@example.test")
    outsider = make_client(app, "Outsider Dave", "dave@example.test")

    # 3. Setup profiles with reviewed requirements and consent
    for c in (host, guest_a, guest_b, outsider):
        p_res = c.patch(
            "/api/profile",
            json={
                "allergy_status": "none",
                "halal_policy": "none",
                "requirements_reviewed": True,
                "memory_enabled": True,
                "sensitive_data_consent": True,
                "max_budget": 50,
            },
        )
        assert p_res.status_code == 200

    # 4. Host creates a table (room) and gets an invite token
    room_res = host.post("/api/rooms", json={"name": "Friday Feast Crew"})
    assert room_res.status_code == 201
    room_data = room_res.json()
    room_id = room_data["room"]["id"]
    invite_token = room_data["invite_token"]

    # 5. Pre-join preview check: Outsider previews without joining or data leakage
    prev_res = outsider.get(f"/api/rooms/preview?token={invite_token}")
    assert prev_res.status_code == 200
    prev_data = prev_res.json()
    assert prev_data["room_id"] == room_id
    assert prev_data["name"] == "Friday Feast Crew"
    assert prev_data["member_count"] == 1
    assert prev_data["owner_name"] == "Host Alice"
    assert "members" not in prev_data and "meals" not in prev_data

    # 6. GuestA, GuestB, and Outsider join the room
    for g in (guest_a, guest_b, outsider):
        j_res = g.post("/api/rooms/join", json={"token": invite_token})
        assert j_res.status_code == 200

    # 7. Start a meal with a 3-person subset (Host, GuestA, GuestB)
    now = datetime.now(timezone.utc)
    tomorrow = now + timedelta(days=1)
    meal_time = tomorrow.replace(hour=4, minute=30, second=0, microsecond=0)
    answer_deadline = meal_time - timedelta(minutes=40)
    decision_deadline = meal_time - timedelta(minutes=15)

    meal_payload = {
        "kind": "lunch",
        "meal_at": meal_time.isoformat(),
        "answer_by": answer_deadline.isoformat(),
        "decision_by": decision_deadline.isoformat(),
        "duration_minutes": 60,
        "location_label": "Bangsar",
        "latitude": 3.13,
        "longitude": 101.67,
        "radius_km": 10,
        "participant_ids": [host.user_id, guest_a.user_id, guest_b.user_id],
        "idempotency_key": "journey-meal-01",
    }
    meal_res = host.post(f"/api/rooms/{room_id}/meals", json=meal_payload)
    assert meal_res.status_code == 201
    meal = meal_res.json()
    meal_id = meal["id"]
    assert meal["original_invited_count"] == 3

    # 8. Check schedule conflict detection:
    # If Host creates an overlapping second meal with GuestA, a non-leaking conflict notice appears
    conflict_payload = {
        "kind": "lunch",
        "meal_at": (meal_time + timedelta(minutes=15)).isoformat(),
        "answer_by": answer_deadline.isoformat(),
        "decision_by": decision_deadline.isoformat(),
        "duration_minutes": 45,
        "location_label": "Bangsar",
        "latitude": 3.13,
        "longitude": 101.67,
        "radius_km": 10,
        "participant_ids": [host.user_id, guest_a.user_id],
        "idempotency_key": "journey-conflict-01",
    }
    conf_res = host.post(f"/api/rooms/{room_id}/meals", json=conflict_payload)
    assert conf_res.status_code == 201
    conf_view = conf_res.json()
    assert len(conf_view["schedule_conflicts"]) > 0
    assert "invited participant" in conf_view["schedule_conflicts"][0]["message"]
    # Cancel the dummy conflicting meal
    host.post(
        f"/api/meals/{conf_view['id']}/cancel",
        json={"expected_revision": conf_view["revision"]},
    )

    # 9. Individual check-ins on Meal 1 with time constraints (must_leave_by)
    for diner, craving in [
        (host, "something soupy"),
        (guest_a, "warm noodles"),
        (guest_b, "anything good"),
    ]:
        v = diner.get(f"/api/meals/{meal_id}").json()
        chk = diner.put(
            f"/api/meals/{meal_id}/response",
            json={
                "expected_response_revision": v["my_response_revision"],
                "attendance": "join",
                "craving": craving,
                "must_leave_by": "14:00",
                "budget": 40,
                "requirements_confirmed": True,
                "ready": True,
            },
        )
        assert chk.status_code == 200

    # 10. Generate shortlist through actual deterministic ranker
    curr = host.get(f"/api/meals/{meal_id}").json()
    gen_res = host.post(
        f"/api/meals/{meal_id}/generate",
        json={"expected_revision": curr["revision"]},
    )
    assert gen_res.status_code == 200, gen_res.text
    shortlist_meal = gen_res.json()
    assert shortlist_meal["status"] == "shortlisted"
    options = shortlist_meal["result"]["options"]
    assert len(options) >= 1
    selected_option = options[0]
    opt_id = selected_option["id"]

    # 11. Verify recommendation archive persisted
    history_res = host.get(f"/api/meals/{meal_id}/recommendation-history")
    assert history_res.status_code == 200
    archives = history_res.json()
    assert len(archives) >= 1
    assert archives[0]["revision"] == shortlist_meal["revision"]
    assert archives[0]["result"]["options"][0]["id"] == opt_id

    # 12. Participant reports data error during shortlist review
    report_res = guest_b.post(
        f"/api/meals/{meal_id}/report-data-error",
        json={
            "outlet_id": selected_option["outlet_id"],
            "category": "dietary_claim",
            "description": "Cross-contact risk with sesame is not noted on the menu.",
        },
    )
    assert report_res.status_code == 200
    assert report_res.json()["status"] == "investigating"
    with store.transaction() as db:
        # Zero taste observations created by data error reports
        assert (
            db.execute(
                "SELECT COUNT(*) FROM observations WHERE user_id=?",
                (guest_b.user_id,),
            ).fetchone()[0]
            == 0
        )

    # 13. Voting with private veto and resolution
    # Host approves
    host.post(
        f"/api/meals/{meal_id}/votes",
        json={
            "option_id": opt_id,
            "expected_revision": shortlist_meal["revision"],
            "choice": "works",
        },
    )
    # GuestA casts private veto "Cannot eat here"
    guest_a.post(
        f"/api/meals/{meal_id}/votes",
        json={
            "option_id": opt_id,
            "expected_revision": shortlist_meal["revision"],
            "choice": "cannot_eat",
            "reason": "PRIVATE_DIETARY_REASON",
        },
    )
    # GuestB uses group choice delegation
    guest_b.post(
        f"/api/meals/{meal_id}/delegation",
        json={"expected_revision": shortlist_meal["revision"], "enabled": True},
    )

    # Host attempts selection: blocked by private veto!
    blocked_sel = host.post(
        f"/api/meals/{meal_id}/select",
        json={
            "option_id": opt_id,
            "expected_revision": shortlist_meal["revision"],
            "choice_mode": "manual",
        },
    )
    assert blocked_sel.status_code == 409

    # Verify GuestA's private reason is not visible to host
    host_view = host.get(f"/api/meals/{meal_id}").json()
    assert "PRIVATE_DIETARY_REASON" not in str(host_view)

    # GuestA resolves veto to "Works for me"
    guest_a.post(
        f"/api/meals/{meal_id}/votes",
        json={
            "option_id": opt_id,
            "expected_revision": shortlist_meal["revision"],
            "choice": "works",
        },
    )

    # 14. Host confirms selection using PRD tie_break choice mode
    sel_res = host.post(
        f"/api/meals/{meal_id}/select",
        json={
            "expected_revision": shortlist_meal["revision"],
            "choice_mode": "tie_break",
        },
    )
    assert sel_res.status_code == 200
    confirmed = sel_res.json()
    assert confirmed["status"] == "selected"
    assert confirmed["decision"]["choice_mode"] == "tie_break"
    assert confirmed["decision"]["checked"] is True

    # 15. Advance time past planned finish -> transition to awaiting_feedback
    finish_time = meal_time + timedelta(minutes=61)
    monkeypatch.setattr("dining.api.now", lambda value=finish_time: value)
    notif_svc.tick(at=finish_time)
    awaiting = host.get(f"/api/meals/{meal_id}").json()
    assert awaiting["status"] == "awaiting_feedback"

    # 16. Advance time past planned finish + 60m -> notification prompt ready
    prompt_time = meal_time + timedelta(minutes=125)
    monkeypatch.setattr("dining.api.now", lambda value=prompt_time: value)
    notif_svc.tick(at=prompt_time)
    inbox = host.get("/api/notifications").json()
    assert any(n["kind"] == "feedback_available" for n in inbox["notifications"])

    # 17. Submit post-meal feedback
    # Host confirms actual visit: positive experience
    fb_host = host.post(
        f"/api/meals/{meal_id}/feedback",
        json={
            "visited": True,
            "outcome": "ate_here",
            "option_id": opt_id,
            "enjoyment": "enjoyed",
            "repeat_intent": "yes",
            "influences": ["taste", "portion"],
            "dish_text": "Signature noodle set",
            "fairness": "yes",
        },
    )
    assert fb_host.status_code == 200

    # GuestA reports plans changed to an alternate branch
    fb_guest_a = guest_a.post(
        f"/api/meals/{meal_id}/feedback",
        json={
            "visited": False,
            "outcome": "somewhere_else",
            "option_id": opt_id,
            "alternate_outlet_name": "Cozy Cafe Bangsar",
            "fairness": "yes",
        },
    )
    assert fb_guest_a.status_code == 200

    # Verify observations: Host has 1 confirmed visit observation; GuestA has 0 negative observations
    with store.transaction() as db:
        host_obs = db.execute(
            "SELECT * FROM observations WHERE user_id=?", (host.user_id,)
        ).fetchall()
        assert len(host_obs) == 1
        guest_obs = db.execute(
            "SELECT * FROM observations WHERE user_id=?", (guest_a.user_id,)
        ).fetchall()
        assert len(guest_obs) == 0

    # 18. Simulate 2 additional confirmed visits for Host on other meals to trigger repeat pattern proposal
    for i in (2, 3):
        extra_id = f"extra-meal-{i}"
        with store.transaction() as db:
            from dining.core.store import encode

            db.execute(
                "INSERT INTO meals(id,room_id,organizer_id,payload,created_at,idempotency_key,original_answer_by,original_decision_by,original_invited_count,status,decision,result) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    extra_id,
                    room_id,
                    host.user_id,
                    encode(
                        {
                            **meal_payload,
                            "meal_at": (meal_time - timedelta(days=i)).isoformat(),
                        }
                    ),
                    now.isoformat(),
                    f"idemp-{i}",
                    now.isoformat(),
                    now.isoformat(),
                    3,
                    "awaiting_feedback",
                    encode(confirmed["decision"]),
                    encode(shortlist_meal["result"]),
                ),
            )
            db.execute(
                "INSERT INTO participants(meal_id,user_id) VALUES(?,?)",
                (extra_id, host.user_id),
            )
        fb_extra = host.post(
            f"/api/meals/{extra_id}/feedback",
            json={
                "visited": True,
                "outcome": "ate_here",
                "option_id": opt_id,
                "enjoyment": "enjoyed",
                "repeat_intent": "yes",
            },
        )
        assert fb_extra.status_code == 200

    # Verify a preference proposal was generated for the repeated venue
    learning_res = host.get("/api/learning")
    assert learning_res.status_code == 200
    learning_data = learning_res.json()
    assert len(learning_data["proposals"]) >= 1
    prop = learning_data["proposals"][0]
    assert prop["status"] == "pending"

    # User reviews and accepts proposal
    accept_prop = host.post(
        f"/api/learning/proposals/{prop['id']}", json={"decision": "accept"}
    )
    assert accept_prop.status_code == 200

    # 19. Advance time past 7 days -> meal closes
    closed_time = meal_time + timedelta(days=8)
    notif_svc.tick(at=closed_time)
    closed_meal = host.get(f"/api/meals/{meal_id}").json()
    assert closed_meal["status"] == "closed"

    # 20. Account export verifies complete data
    export_res = host.get("/api/export")
    assert export_res.status_code == 200
    export_data = export_res.json()
    assert export_data["user"]["id"] == host.user_id
    assert len(export_data["room_memberships"]) >= 1
    assert "profile" in export_data
    assert len(export_data["feedback"]) >= 1

    # 21. Account deletion cascades private data while preserving shared table
    del_res = host.delete("/api/account")
    assert del_res.status_code == 200
    with store.transaction() as db:
        # Host user and personal records scrubbed
        assert (
            db.execute(
                "SELECT COUNT(*) FROM users WHERE id=?", (host.user_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM feedback WHERE user_id=?", (host.user_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM observations WHERE user_id=?",
                (host.user_id,),
            ).fetchone()[0]
            == 0
        )
        # Room was owned by host, so it is archived rather than deleted
        room_row = db.execute(
            "SELECT archived FROM rooms WHERE id=?", (room_id,)
        ).fetchone()
        assert room_row["archived"] == 1

    app.state.store.close()
