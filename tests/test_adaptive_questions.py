from copy import deepcopy


def test_m09_is_issued_only_when_a_choice_can_change_the_preliminary_order():
    from test_recommendation import ready_catalog, snapshot

    from dining.adaptive import build_m09_question
    from dining.recommendation import Recommender

    state = snapshot()
    for index, person in enumerate(state["participants"]):
        person["user_id"] = f"person-{index}"
        person["response"].update(
            craving="anything",
            cuisines=[],
            dish_families=[],
            flavour_tags=[],
        )

    question = build_m09_question(Recommender(ready_catalog()), state, "person-0")

    assert question["question_id"] == "M09"
    assert question["dimension"] == "cuisines"
    assert 2 <= len(question["choices"]) <= 3
    assert len(set(question["choices"])) == len(question["choices"])
    assert "outlet" not in str(question).casefold()

    decided = deepcopy(state)
    decided["participants"][0]["response"]["cuisines"] = question["choices"][:1]
    assert build_m09_question(Recommender(ready_catalog()), decided, "person-0") is None


def test_owner_can_answer_one_server_issued_m09_and_stale_answers_are_rejected(
    tmp_path,
):
    from test_api import checkin, create_meal, make_client, setup_room

    from webapp import create_app

    app = create_app(tmp_path / "adaptive.sqlite3", demo_mode=True)
    people = [make_client(app, name) for name in ("Host", "Guest", "Outside")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room)
    for person in people[:2]:
        checkin(person, meal["id"], cuisines=[], craving="anything")

    current = people[0].get(f"/api/meals/{meal['id']}").json()
    body = {
        "expected_revision": current["revision"],
        "expected_response_revision": current["my_response_revision"],
    }
    url = f"/api/meals/{meal['id']}/adaptive-question"
    assert people[2].post(url, json=body).status_code == 404

    issued = people[0].post(url, json=body)
    assert issued.status_code == 200, issued.text
    question = issued.json()
    assert question["status"] == "available"
    assert question["question_id"] == "M09"
    assert len(question["choices"]) in (2, 3)
    assert "name" not in question and "options" not in question

    # Both current diners may receive one private opportunity from this revision.
    other = people[1].get(f"/api/meals/{meal['id']}").json()
    other_question = (
        people[1]
        .post(
            url,
            json={
                "expected_revision": other["revision"],
                "expected_response_revision": other["my_response_revision"],
            },
        )
        .json()
    )
    assert other_question["status"] == "available"

    selected = question["choices"][0]
    answered = people[0].post(
        f"{url}/{question['request_id']}", json={"choice": selected, "skip": False}
    )
    assert answered.status_code == 200, answered.text
    saved = answered.json()
    assert saved["my_response_revision"] == current["my_response_revision"] + 1
    assert saved["my_response"]["cuisines"] == [selected]
    assert saved["my_response"]["taste_input_mode"] == "structured"
    assert saved["my_response"]["ready"] is True
    assert (
        people[0]
        .post(
            f"{url}/{question['request_id']}",
            json={"choice": selected, "skip": False},
        )
        .status_code
        == 409
    )
    repeated = people[0].post(
        url,
        json={
            "expected_revision": saved["revision"],
            "expected_response_revision": saved["my_response_revision"],
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "already_answered"

    # The first answer changes the meal context, so the other's old question is stale.
    stale = people[1].post(
        f"{url}/{other_question['request_id']}",
        json={"choice": other_question["choices"][0], "skip": False},
    )
    assert stale.status_code == 409
    assert (
        people[1].get(f"/api/meals/{meal['id']}").json()["my_response"]["cuisines"]
        == []
    )
    refreshed = people[1].get(f"/api/meals/{meal['id']}").json()
    expired = people[1].post(
        url,
        json={
            "expected_revision": refreshed["revision"],
            "expected_response_revision": refreshed["my_response_revision"],
        },
    )
    assert expired.json()["status"] == "already_stale"
    app.state.store.close()


def test_skipping_m09_preserves_the_saved_answer_and_uses_no_second_question(tmp_path):
    from test_api import checkin, create_meal, make_client, setup_room

    from webapp import create_app

    app = create_app(tmp_path / "skip.sqlite3", demo_mode=True)
    people = [make_client(app, name) for name in ("One", "Two")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room)
    for person in people:
        checkin(person, meal["id"], cuisines=[], craving="anything")
    before = people[0].get(f"/api/meals/{meal['id']}").json()
    url = f"/api/meals/{meal['id']}/adaptive-question"
    question = (
        people[0]
        .post(
            url,
            json={
                "expected_revision": before["revision"],
                "expected_response_revision": before["my_response_revision"],
            },
        )
        .json()
    )
    skipped = people[0].post(
        f"{url}/{question['request_id']}", json={"choice": None, "skip": True}
    )
    assert skipped.status_code == 200
    after = skipped.json()
    assert after["revision"] == before["revision"]
    assert after["my_response_revision"] == before["my_response_revision"]
    assert after["my_response"] == before["my_response"]
    again = people[0].post(
        url,
        json={
            "expected_revision": after["revision"],
            "expected_response_revision": after["my_response_revision"],
        },
    )
    assert again.json()["status"] == "already_skipped"
    app.state.store.close()
