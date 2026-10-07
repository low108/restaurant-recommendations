import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dish_translations.py"
spec = importlib.util.spec_from_file_location("dish_translations", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ALLOWED = {"o1"}
RAW = {
    "menu_items": [
        {"item_id": "i1", "outlet_id": "o1", "review_status": "reviewed"},
        {"item_id": "i2", "outlet_id": "o2", "review_status": "reviewed"},
        {"item_id": "i3", "outlet_id": "o1", "review_status": "quarantined"},
    ]
}


def entry(item_id="i1", **overrides):
    base = {
        "item_id": item_id,
        "translations": {"en": ["beef pho"], "ms": ["pho daging"], "zh": ["牛肉河粉"]},
        "evidence": ["name"],
        "confidence": "high",
        "notes": "",
        "review": {"status": "needs_review", "reviewer": None},
    }
    base.update(overrides)
    return base


def check(*entries):
    patch = {
        "patch_version": tool.PATCH_VERSION,
        "input": {"sha256": "abc"},
        "item_updates": list(entries),
    }
    return tool.validate_patch(patch, RAW, ALLOWED, "abc")


def test_clean_entry_has_no_errors():
    errors, _ = check(entry())
    assert errors == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"translations": {"fr": ["boeuf"]}},
        {"translations": {"en": [""]}},
        {"translations": {"en": ["x" * 61]}},
        {"translations": {"en": [f"dish {n}" for n in range(7)]}},
        {"translations": {"en": ["see https://example.com"]}},
        {"translations": {"en": ["a tasty dish. enjoy it"]}},
        {"translations": {"en": ["halal beef pho"]}},
        {"translations": {"zh": ["素食河粉"]}},
        {"translations": {"en": ["No Pork soup"]}},
        {"evidence": []},
        {"evidence": ["guess"]},
        {"confidence": "low", "review": {"status": "approved", "reviewer": "owner"}},
        {"review": {"status": "approved", "reviewer": None}},
    ],
)
def test_validate_rejects(overrides):
    errors, _ = check(entry(**overrides))
    assert errors


def test_validate_rejects_unknown_out_of_scope_and_unreviewed_items():
    errors, _ = check(entry("nope"), entry("i2"), entry("i3"))
    assert len(errors) >= 3


def test_validate_rejects_duplicate_entry_and_repeated_term():
    errors, _ = check(entry(), entry())
    assert any("duplicate" in line for line in errors)
    errors, _ = check(entry(translations={"en": ["Pho", "pho"]}))
    assert any("repeated" in line for line in errors)


def test_empty_entry_needs_a_reason():
    empty = entry(translations={"en": [], "ms": [], "zh": []}, evidence=[])
    errors, warnings = check(empty)
    assert errors == []
    assert any("without a reason" in line for line in warnings)


def test_apply_refuses_to_overwrite(tmp_path, monkeypatch):
    out = tmp_path / "catalog.json"
    out.write_text("{}")
    catalog = tmp_path / "in.json"
    catalog.write_text(json.dumps({"version": "1", **RAW}))
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"allowed_outlet_ids": ["o1"]}))
    patch = tmp_path / "p.json"
    patch.write_text(
        json.dumps(
            {
                "patch_version": tool.PATCH_VERSION,
                "input": {"sha256": tool.sha256(catalog)},
                "item_updates": [entry()],
            }
        )
    )
    args = type(
        "A",
        (),
        {
            "patch": patch,
            "catalog": catalog,
            "outlet_manifest": manifest,
            "out": out,
            "version": "2",
        },
    )
    assert tool.apply(args) == 2
    assert out.read_text() == "{}"
