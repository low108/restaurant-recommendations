"""Deterministic, private M09 opportunities derived from current viable options."""

from __future__ import annotations

from copy import deepcopy

from dining.recommendation.engine import Recommender

VERSION = "adaptive-m09-v1"


def _signature(result: dict) -> tuple:
    return tuple(
        (
            option.get("outlet_id"),
            tuple(item.get("id") for item in option.get("menu_items", [])),
        )
        for option in result.get("options", [])
    )


def build_m09_question(
    recommender: Recommender, snapshot: dict, user_id: str
) -> dict | None:
    """Return one question only when explicit cuisine choices change the result.

    The response contains ontology labels only. Restaurant identities, menu prose,
    private answers and scores never leave this boundary.
    """
    person = next(
        (p for p in snapshot.get("participants", []) if p.get("user_id") == user_id),
        None,
    )
    if person is None:
        return None
    response = person.get("response", {})
    if (
        response.get("cuisines")
        or response.get("dish_families")
        or response.get("flavour_tags")
        or response.get("craving", "").casefold().strip()
        not in {"", "anything", "anything works", "open", "surprise me"}
    ):
        return None

    baseline = recommender(snapshot)
    options = baseline.get("options", [])
    if (
        baseline.get("status") != "shortlisted"
        or len(options) < 2
        or not any(option.get("fit_confidence") == "limited" for option in options)
    ):
        return None

    candidates = []
    for option in options:
        for cuisine in option.get("cuisines", []):
            if (
                isinstance(cuisine, str)
                and 1 <= len(cuisine) <= 60
                and cuisine not in candidates
            ):
                candidates.append(cuisine)
    # This bounds local counterfactual work even for an unusually broad catalog.
    candidates = candidates[:12]
    signatures: dict[tuple, str] = {}
    for cuisine in candidates:
        changed = deepcopy(snapshot)
        target = next(p for p in changed["participants"] if p.get("user_id") == user_id)
        target["response"] = {
            **target["response"],
            "taste_input_mode": "structured",
            "cuisines": [cuisine],
        }
        result = recommender(changed)
        if result.get("status") == "shortlisted" and result.get("options"):
            signatures.setdefault(_signature(result), cuisine)
    choices = list(signatures.values())[:3]
    if len(choices) < 2:
        return None
    return {
        "question_id": "M09",
        "question_version": VERSION,
        "prompt": "Which sounds better for this meal?",
        "dimension": "cuisines",
        "choices": choices,
        "optional": True,
        "purpose": "These choices lead to different preliminary shortlist orders.",
    }
