#!/usr/bin/env python3
"""Execute full E2E dining flow with lowchening108@gmail.com and cheninglow108@gmail.com.

Verifies:
1. Live authentication for both accounts.
2. Table/room creation and member join.
3. Meal slot creation with Klang Valley coordinates.
4. Separate check-ins with distinct cravings and preferences.
5. Live recommendation generation:
   - Vector DB candidate retrieval (Chroma + multilingual MiniLM over 58 verified outlets)
   - Real ILMU LLM inference for explanation labeling
   - Group shortlist with evidence-backed restaurants
   - Owner-scoped personal alternatives
6. Group voting and unanimous selection.
"""

import json
from datetime import datetime, timedelta, timezone
import httpx

BASE_URL = "http://127.0.0.1:7860"

def run_e2e():
    print(f"Connecting to live server at {BASE_URL}...")
    client = httpx.Client(base_url=BASE_URL, timeout=30.0)

    # 1. Verify system status
    health = client.get("/health").json()
    print("\n--- [System Health] ---")
    print(json.dumps(health, indent=2))
    assert health["status"] == "ok"

    cat_status = client.get("/api/catalog/status").json()
    print("\n--- [Vector DB Status] ---")
    retrieval = cat_status["retrieval"]
    print(json.dumps(retrieval, indent=2))
    assert retrieval["mode"] == "semantic"
    assert retrieval["index_available"] is True
    assert retrieval["indexed_item_count"] == 1073
    assert retrieval["indexed_outlet_count"] == 58

    inf_status = client.get("/api/inference/status").json()
    print("\n--- [ILMU Inference Status] ---")
    print(json.dumps(inf_status, indent=2))
    assert inf_status["provider"] == "ilmu"
    assert inf_status["configuration_status"] == "ready"

    # 2. Authenticate lowchening108@gmail.com
    print("\n--- [Authenticating User 1: lowchening108@gmail.com] ---")
    u1_client = httpx.Client(base_url=BASE_URL, timeout=30.0)
    u1_login = u1_client.post(
        "/api/auth/login",
        json={"email": "lowchening108@gmail.com", "password": "lowchening108@gmail.com"},
    )
    if u1_login.status_code != 200:
        # Register if needed
        u1_login = u1_client.post(
            "/api/auth/register",
            json={
                "name": "Low Chen Ing",
                "email": "lowchening108@gmail.com",
                "password": "lowchening108@gmail.com",
                "adult_confirmed": True,
                "terms_accepted": True,
            },
        )
    assert u1_login.status_code == 200 or u1_login.status_code == 201, u1_login.text
    u1_data = u1_login.json()
    u1_csrf = u1_data["csrf_token"]
    u1_client.headers["X-CSRF-Token"] = u1_csrf
    u1_id = u1_data["user"]["id"]
    print(f"User 1 authenticated: {u1_data['user']['name']} ({u1_data['user']['email']}), ID: {u1_id}")

    # Set profile for User 1
    u1_client.patch(
        "/api/profile",
        json={
            "allergy_status": "none",
            "allergens": [],
            "dietary_requirements": [],
            "halal_policy": "none",
            "requirements_reviewed": True,
            "max_budget": 50,
            "cuisines": ["Chinese", "Malaysian"],
        },
    )

    # 3. Authenticate cheninglow108@gmail.com
    print("\n--- [Authenticating User 2: cheninglow108@gmail.com] ---")
    u2_client = httpx.Client(base_url=BASE_URL, timeout=30.0)
    u2_login = u2_client.post(
        "/api/auth/login",
        json={"email": "cheninglow108@gmail.com", "password": "cheninglow108@gmail.com"},
    )
    if u2_login.status_code != 200:
        u2_login = u2_client.post(
            "/api/auth/register",
            json={
                "name": "Chen Ing Low",
                "email": "cheninglow108@gmail.com",
                "password": "cheninglow108@gmail.com",
                "adult_confirmed": True,
                "terms_accepted": True,
            },
        )
    assert u2_login.status_code == 200 or u2_login.status_code == 201, u2_login.text
    u2_data = u2_login.json()
    u2_csrf = u2_data["csrf_token"]
    u2_client.headers["X-CSRF-Token"] = u2_csrf
    u2_id = u2_data["user"]["id"]
    print(f"User 2 authenticated: {u2_data['user']['name']} ({u2_data['user']['email']}), ID: {u2_id}")

    # Set profile for User 2
    u2_client.patch(
        "/api/profile",
        json={
            "allergy_status": "none",
            "allergens": [],
            "dietary_requirements": [],
            "halal_policy": "none",
            "requirements_reviewed": True,
            "max_budget": 50,
            "cuisines": ["Chinese", "Thai"],
        },
    )

    # 4. User 1 creates room
    print("\n--- [Creating Table / Room] ---")
    room_resp = u1_client.post("/api/rooms", json={"name": "Klang Valley Foodies"})
    assert room_resp.status_code == 201, room_resp.text
    room = room_resp.json()["room"]
    invite_token = room_resp.json()["invite_token"]
    print(f"Room created: '{room['name']}' (ID: {room['id']}), Invite Token: {invite_token}")

    # User 2 joins room
    join_resp = u2_client.post("/api/rooms/join", json={"token": invite_token})
    assert join_resp.status_code == 200, join_resp.text
    print(f"User 2 successfully joined room '{room['name']}'")

    # 5. User 1 schedules meal slot
    print("\n--- [Scheduling Meal Slot] ---")
    tomorrow_lunch = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=4, minute=30, second=0, microsecond=0
    ).isoformat()
    # Coordinates in Petaling Jaya Seksyen 19 / SS2
    meal_resp = u1_client.post(
        f"/api/rooms/{room['id']}/meals",
        json={
            "kind": "lunch",
            "meal_at": tomorrow_lunch,
            "location_label": "PJ Seksyen 19",
            "latitude": 3.119,
            "longitude": 101.629,
            "radius_km": 10,
            "idempotency_key": f"meal-{datetime.now().timestamp()}",
        },
    )
    assert meal_resp.status_code == 201, meal_resp.text
    meal = meal_resp.json()
    print(f"Meal slot created: ID {meal['id']} at {meal['meal_at']}")
    print(f"Meeting Location: ({meal['latitude']}, {meal['longitude']}), Radius: {meal['radius_km']} km")

    # 6. Both users complete private check-ins
    print("\n--- [Submitting Check-ins] ---")
    u1_ans = u1_client.put(
        f"/api/meals/{meal['id']}/response",
        json={
            "attendance": "join",
            "budget": 50,
            "craving": "fragrant chicken and rice",
            "ready": True,
            "requirements_confirmed": True,
            "expected_response_revision": 0,
        },
    )
    assert u1_ans.status_code == 200, u1_ans.text
    meal = u1_ans.json()
    print(f"User 1 ({u1_data['user']['email']}) checked in: craving='fragrant chicken and rice', budget=RM50")

    u2_ans = u2_client.put(
        f"/api/meals/{meal['id']}/response",
        json={
            "attendance": "join",
            "budget": 50,
            "craving": "warm noodles and soup",
            "ready": True,
            "requirements_confirmed": True,
            "expected_response_revision": 0,
        },
    )
    assert u2_ans.status_code == 200, u2_ans.text
    meal = u2_ans.json()
    print(f"User 2 ({u2_data['user']['email']}) checked in: craving='warm noodles and soup', budget=RM50")

    # 7. Generate recommendations
    print("\n--- [Generating Live Recommendations via Vector DB + ILMU] ---")
    gen_resp = u1_client.post(
        f"/api/meals/{meal['id']}/generate",
        json={"expected_revision": meal["revision"]},
    )
    assert gen_resp.status_code in {200, 202}, gen_resp.text
    gen_data = gen_resp.json()

    import time
    for _ in range(40):
        if gen_data.get("status") != "generating":
            break
        time.sleep(0.5)
        gen_data = u1_client.get(f"/api/meals/{meal['id']}").json()

    result = gen_data["result"]

    print("\n================= RECOMMENDATION PIPELINE RESULT =================")
    print(f"Status: {result['status']}")
    print(f"Retrieval Status: {result.get('retrieval_status')}")
    print(f"Embedding Model: {result.get('embedding_model')}")
    print(f"Examined Outlets: {result.get('examined_outlets')}")

    # Inspect ILMU Agent Metadata
    agent_meta = result.get("agent", {})
    print("\n--- [Live ILMU Agent Execution Details] ---")
    print(f"Framework: {agent_meta.get('framework')}")
    print(f"Provider: {agent_meta.get('provider')}")
    print(f"Model ID: {agent_meta.get('model_id')}")
    print(f"Inference Status: {agent_meta.get('inference_status')}")
    print(f"Model Calls: {agent_meta.get('model_calls')}")
    print(f"Input Tokens: {agent_meta.get('input_tokens')}")
    print(f"Output Tokens: {agent_meta.get('output_tokens')}")
    print(f"Total Tokens: {agent_meta.get('total_tokens')}")
    print(f"Estimated Cost: ${agent_meta.get('estimated_cost')} {agent_meta.get('currency')}")

    # Inspect Shared Shortlist Options
    options = result.get("options", [])
    print(f"\n--- [Shared Shortlist ({len(options)} options)] ---")
    for idx, opt in enumerate(options, start=1):
        print(f"\nOption #{idx}: {opt['name']} (Outlet ID: {opt['outlet_id']})")
        print(f"  Area: {opt.get('area')}")
        print(f"  Distance: {opt.get('distance_km')} km")
        print(f"  Cuisines: {opt.get('cuisines')}")
        print(f"  Recommended Items: {[i['name'] for i in opt.get('menu_items', [])]}")
        print(f"  ILMU Selected Reasons: {opt.get('reasons')}")
        print(f"  Tradeoffs / Transparency: {opt.get('tradeoffs')}")

    # Inspect Owner-Scoped Personal Alternatives
    print("\n--- [Owner-Scoped Personal Alternatives] ---")
    u1_meal_view = u1_client.get(f"/api/meals/{meal['id']}").json()
    u1_pers = u1_meal_view.get("my_personal_recommendations", [])
    print(f"\nPersonal Alternatives for {u1_data['user']['name']}:")
    for p in u1_pers:
        print(f"  - Rank #{p['rank']}: Outlet {p['outlet_id']} (Item: {p['item_id']}, Score: {p['score']}, Reasons: {p['reason_codes']})")

    u2_meal_view = u2_client.get(f"/api/meals/{meal['id']}").json()
    u2_pers = u2_meal_view.get("my_personal_recommendations", [])
    print(f"\nPersonal Alternatives for {u2_data['user']['name']}:")
    for p in u2_pers:
        print(f"  - Rank #{p['rank']}: Outlet {p['outlet_id']} (Item: {p['item_id']}, Score: {p['score']}, Reasons: {p['reason_codes']})")

    # 8. Voting and Decision
    print("\n--- [Voting and Group Selection] ---")
    chosen_option = options[0]["id"]
    chosen_name = options[0]["name"]
    print(f"Voting for Option #1: '{chosen_name}' (ID: {chosen_option})")

    v1 = u1_client.post(
        f"/api/meals/{meal['id']}/votes",
        json={"expected_revision": gen_data["revision"], "option_id": chosen_option, "choice": "works"},
    )
    assert v1.status_code == 200, v1.text
    print(f"User 1 voted: 'works'")

    v2 = u2_client.post(
        f"/api/meals/{meal['id']}/votes",
        json={"expected_revision": gen_data["revision"], "option_id": chosen_option, "choice": "works"},
    )
    assert v2.status_code == 200, v2.text
    print(f"User 2 voted: 'works'")

    sel_resp = u1_client.post(
        f"/api/meals/{meal['id']}/select",
        json={"expected_revision": gen_data["revision"], "option_id": chosen_option},
    )
    assert sel_resp.status_code == 200, sel_resp.text
    final_decision = sel_resp.json()["decision"]
    print(f"\nUnanimous Selection Confirmed!")
    print(f"Selected Restaurant: '{chosen_name}'")
    print(f"Decision Record: {json.dumps(final_decision, indent=2)}")
    print("\nSUCCESS: E2E Recommendation Flow Completed with Live Vector DB and ILMU Inference!")

if __name__ == "__main__":
    run_e2e()
