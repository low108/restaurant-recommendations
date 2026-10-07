"""Collect golden-case outcomes into var/golden-results/<suite>.json for reporting."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESULTS: dict[str, dict] = {}
CASE_ID = re.compile(r"test_(gt|p)_?(\d{3})", re.IGNORECASE)


def pytest_collection_modifyitems(config, items):
    """Golden suites are opt-in: they record product gaps and are expected to fail today.

    Run with:  DINING_GOLDEN=1 .venv/bin/python -m pytest tests/golden -q
    """
    if os.environ.get("DINING_GOLDEN") == "1":
        return
    skip = pytest.mark.skip(reason="golden suite is opt-in; set DINING_GOLDEN=1")
    for item in items:
        if "tests/golden/" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)


def pytest_runtest_logreport(report):
    match = CASE_ID.search(report.nodeid)
    if not match or os.environ.get("DINING_GOLDEN") != "1":
        return
    prefix = "GT" if match.group(1).lower() == "gt" else "P"
    case = f"{prefix}-{match.group(2)}"
    suite = "fixture" if prefix == "GT" else "production"
    entry = RESULTS.setdefault(suite, {}).setdefault(
        case, {"outcome": "passed", "detail": ""}
    )
    if report.outcome != "passed" and report.when in {"setup", "call"}:
        entry["outcome"] = "skipped" if report.skipped else "failed"
        text = str(report.longrepr)
        lines = [line for line in text.splitlines() if line.startswith("E ")]
        entry["detail"] = (
            " | ".join(line[2:].strip() for line in lines[:4])[:600] or text[-400:]
        )


def pytest_sessionfinish(session, exitstatus):
    out = ROOT / "var/golden-results"
    out.mkdir(parents=True, exist_ok=True)
    for suite, cases in RESULTS.items():
        (out / f"{suite}.json").write_text(
            json.dumps(dict(sorted(cases.items())), indent=2) + "\n", encoding="utf-8"
        )
