import json

from langchain_core.messages import AIMessage

from dining.inference import InferenceSettings


def test_interpreted_malay_craving_is_an_editable_proposal_not_a_requirement(
    monkeypatch,
):
    from dining.preferences import interpret_preferences

    seen = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            seen.append(messages[1].content)
            return AIMessage(
                content=json.dumps(
                    {
                        "dish_families": ["soup"],
                        "flavour_tags": ["light"],
                        "soft_budget_target": 20,
                        "spice": "mild",
                        "appetite": "light",
                    }
                )
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    result = interpret_preferences(
        InferenceSettings.from_env({}, use_model=True),
        "Nak sup ringan, kurang pedas, sekitar RM20",
    )
    assert result["status"] == "proposed"
    assert result["suggestions"]["dish_families"] == ["soup"]
    assert result["suggestions"]["soft_budget_target"] == 20
    assert "budget" not in result["suggestions"]
    assert "profile" not in seen[0]
    assert result["requires_confirmation"] is True


def test_requirements_and_secrets_stay_out_of_provider_and_invalid_fields_fall_back(
    monkeypatch,
):
    from dining.preferences import interpret_preferences

    settings = InferenceSettings.from_env({}, use_model=True)
    calls = []

    def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("No provider for private or requirement input")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", forbidden)
    for text in (
        "no nuts please",
        "alergi udang",
        "avoid eggs",
        "my password is abcdef",
    ):
        result = interpret_preferences(settings, text)
        assert result["suggestions"] == {}
        assert result["agent"]["model_calls"] == 0
    assert not calls


def test_private_interpretation_requires_consent_scope_and_does_not_save_answers(
    tmp_path, monkeypatch
):
    from test_api import create_meal, make_client, setup_room

    from webapp import create_app

    app = create_app(
        tmp_path / "app.sqlite3",
        demo_mode=True,
        inference_settings=InferenceSettings.from_env({}, use_model=True),
    )
    people = [make_client(app, name) for name in ("Host", "Guest", "Other")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room)
    seen = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            seen.append(messages)
            return AIMessage(
                content='{"dish_families":["soup"],"soft_budget_target":20}'
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    url = f"/api/meals/{meal['id']}/interpret-preferences"
    payload = {
        "text": "Something soupy about RM20",
        "expected_revision": meal["revision"],
        "expected_response_revision": 0,
        "allow_model_processing": True,
    }
    assert people[2].post(url, json=payload).status_code == 404
    assert (
        people[0]
        .post(url, json={**payload, "allow_model_processing": False})
        .status_code
        == 422
    )
    assert len(seen) == 0
    response = people[0].post(url, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["suggestions"]["dish_families"] == ["soup"]
    assert people[0].get(f"/api/meals/{meal['id']}").json()["my_response"] is None
    assert "Something soupy" not in people[1].get(f"/api/meals/{meal['id']}").text
    assert people[0].post(url, json=payload).status_code == 200
    assert people[0].post(url, json=payload).status_code == 429
    assert len(seen) == 2
    app.state.store.close()


def test_confirmed_chips_override_original_prose_in_ranking():
    from test_recommendation import ready_catalog

    from dining.ranking import score_item

    item = (
        ready_catalog()
        .menu_items[0]
        .model_copy(update={"attributes": ("soup", "rich"), "cuisine_tags": ()})
    )
    response = {
        "craving": "soup",
        "taste_input_mode": "structured",
        "dish_families": [],
        "flavour_tags": ["light"],
    }
    fit = score_item(item, {}, response, outlet_id=item.outlet_id)
    assert fit.features["C"].value == 0.2
    response["flavour_tags"] = []
    assert (
        score_item(item, {}, response, outlet_id=item.outlet_id).features["C"].coverage
        == 0
    )


def test_stale_request_or_concurrent_edit_never_returns_a_live_proposal(
    tmp_path, monkeypatch
):
    from test_api import checkin, create_meal, make_client, setup_room

    from webapp import create_app

    app = create_app(
        tmp_path / "app.sqlite3",
        demo_mode=True,
        inference_settings=InferenceSettings.from_env({}, use_model=True),
    )
    people = [make_client(app, n) for n in ("A", "B")]
    room, _ = setup_room(people)
    meal, _ = create_meal(people[0], room)
    seen = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            seen.append(messages)
            checkin(people[1], meal["id"], craving="rice")
            return AIMessage(content='{"dish_families":["soup"]}')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", Provider)
    body = {
        "text": "soup",
        "expected_revision": meal["revision"],
        "expected_response_revision": 0,
        "allow_model_processing": True,
    }
    url = f"/api/meals/{meal['id']}/interpret-preferences"
    assert (
        people[0].post(url, json={**body, "expected_revision": 999}).status_code == 409
    )
    assert not seen
    result = people[0].post(url, json=body)
    assert result.status_code == 409
    assert '"suggestions"' not in result.text
    assert len(seen) == 1
    app.state.store.close()
