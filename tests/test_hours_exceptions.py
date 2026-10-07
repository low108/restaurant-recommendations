"""Fictional, dated service evidence; never use the current weekday as a proxy."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from test_recommendation import ready_catalog, snapshot

from dining.catalog import Catalog
from dining.recommendation import Recommender, _open_for


def dated_catalog():
    raw = ready_catalog().model_dump(mode="json")
    outlet = raw["outlets"][0]
    ids = outlet["source_ids"]
    outlet["opening_hours"] = [
        {
            "weekday": day,
            "opens": "10:00",
            "closes": "23:00",
            "last_order": "21:30",
            "last_order_source_ids": ids,
        }
        for day in range(7)
    ]
    outlet["opening_exceptions_coverage"] = {
        "starts_on": "2026-10-01",
        "ends_on": "2026-10-31",
        "source_ids": ids,
    }
    raw["outlets"] = [outlet]
    raw["menu_items"] = [
        item for item in raw["menu_items"] if item["outlet_id"] == outlet["outlet_id"]
    ]
    return raw


def at(value):
    return datetime.fromisoformat(value + "+08:00")


def exception(raw, on_date, status="closed", intervals=None):
    outlet = raw["outlets"][0]
    outlet["opening_exceptions"] = [
        {
            "on_date": on_date,
            "status": status,
            "intervals": intervals or [],
            "source_ids": outlet["source_ids"],
        }
    ]


def test_dated_closure_overrides_published_weekday_hours():
    raw = dated_catalog()
    exception(raw, "2026-10-06")
    outlet = Catalog.model_validate(raw).outlets[0]
    assert _open_for(outlet, at("2026-10-06T12:00:00"), 60) is False
    assert _open_for(outlet, at("2026-10-07T12:00:00"), 60) is True


def test_dated_override_replaces_weekly_hours_and_kitchen_cutoff():
    raw = dated_catalog()
    exception(
        raw,
        "2026-10-06",
        "published",
        [
            {
                "opens": "12:00",
                "closes": "16:00",
                "last_order": "14:30",
                "last_order_source_ids": raw["outlets"][0]["source_ids"],
            }
        ],
    )
    outlet = Catalog.model_validate(raw).outlets[0]
    assert _open_for(outlet, at("2026-10-06T11:00:00"), 60) is False
    assert _open_for(outlet, at("2026-10-06T14:30:00"), 60) is True
    assert _open_for(outlet, at("2026-10-06T14:31:00"), 60) is False
    assert _open_for(outlet, at("2026-10-06T14:00:00"), 121) is False


def test_unknown_kitchen_and_unscoped_legacy_exception_flag_do_not_pass():
    raw = dated_catalog()
    for interval in raw["outlets"][0]["opening_hours"]:
        interval.pop("last_order")
        interval.pop("last_order_source_ids")
    outlet = Catalog.model_validate(raw).outlets[0]
    assert _open_for(outlet, at("2026-10-06T12:00:00"), 60) is None
    raw = dated_catalog()
    raw["outlets"][0].pop("opening_exceptions_coverage")
    raw["outlets"][0]["holiday_exceptions_known"] = True
    outlet = Catalog.model_validate(raw).outlets[0]
    assert _open_for(outlet, at("2026-10-06T12:00:00"), 60) is None


def test_unknown_exception_and_out_of_range_coverage_do_not_pass():
    raw = dated_catalog()
    exception(raw, "2026-10-06", "unknown")
    outlet = Catalog.model_validate(raw).outlets[0]
    assert _open_for(outlet, at("2026-10-06T12:00:00"), 60) is None
    assert _open_for(outlet, at("2026-11-01T12:00:00"), 60) is None


def test_a_dated_published_interval_can_be_checked_without_known_weekly_hours():
    raw = dated_catalog()
    outlet = raw["outlets"][0]
    outlet.update(
        hours_status="unknown", opening_hours=[], opening_exceptions_coverage=None
    )
    exception(
        raw,
        "2026-10-06",
        "published",
        [
            {
                "opens": "10:00",
                "closes": "16:00",
                "last_order": "14:00",
                "last_order_source_ids": outlet["source_ids"],
            }
        ],
    )
    assert (
        _open_for(Catalog.model_validate(raw).outlets[0], at("2026-10-06T12:00:00"), 60)
        is True
    )


def test_fresh_service_sources_are_included_in_option_evidence():
    raw = dated_catalog()
    state = snapshot()
    day = datetime.fromisoformat(state["meal"]["meal_at"]).date().isoformat()
    outlet = raw["outlets"][0]
    for suffix in ("coverage", "kitchen"):
        source = deepcopy(raw["sources"][0])
        source.update(source_id=suffix, url=f"synthetic://{suffix}")
        raw["sources"].append(source)
    outlet["opening_exceptions_coverage"].update(
        starts_on=day, ends_on=day, source_ids=["coverage"]
    )
    for interval in outlet["opening_hours"]:
        interval["last_order_source_ids"] = ["kitchen"]
    result = Recommender(Catalog.model_validate(raw))(state)
    assert result["options"]
    urls = {source["url"] for source in result["options"][0]["evidence"]}
    assert {"synthetic://coverage", "synthetic://kitchen"} <= urls


def overnight_catalog():
    raw = dated_catalog()
    raw["outlets"][0]["opening_hours"] = [
        {
            "weekday": 0,
            "opens": "20:00",
            "closes": "02:00",
            "closes_next_day": True,
            "last_order": "00:45",
            "last_order_next_day": True,
            "last_order_source_ids": raw["outlets"][0]["source_ids"],
        }
    ]
    return raw


def test_overnight_arrival_last_order_and_finish_use_outlet_timezone():
    outlet = Catalog.model_validate(overnight_catalog()).outlets[0]
    assert _open_for(outlet, at("2026-10-06T00:30:00"), 90) is True
    assert _open_for(outlet, at("2026-10-06T00:46:00"), 30) is False
    assert _open_for(outlet, at("2026-10-06T00:30:00"), 91) is False
    # Same instant supplied in UTC must not change the local service date.
    assert _open_for(outlet, datetime.fromisoformat("2026-10-05T16:30:00Z"), 90) is True


def test_holiday_closure_cuts_off_previous_nights_spillover():
    raw = overnight_catalog()
    exception(raw, "2026-10-06")
    outlet = Catalog.model_validate(raw).outlets[0]
    assert _open_for(outlet, at("2026-10-05T23:00:00"), 60) is True
    assert _open_for(outlet, at("2026-10-05T23:00:00"), 61) is False
    assert _open_for(outlet, at("2026-10-06T00:30:00"), 30) is False


@pytest.mark.parametrize(
    "field", ["opening_exceptions", "opening_exceptions_coverage", "last_order"]
)
@pytest.mark.parametrize(
    "unusable", ["expired", "unknown_rights", "future", "expires_during_meal"]
)
def test_service_evidence_must_be_current_permitted_and_cover_entire_visit(
    field, unusable
):
    raw = dated_catalog()
    state = snapshot()
    local_date = (
        datetime.fromisoformat(state["meal"]["meal_at"])
        .astimezone(at("2026-10-06T12:00:00").tzinfo)
        .date()
        .isoformat()
    )
    source = deepcopy(raw["sources"][0])
    source["source_id"] = "restricted-service-source"
    source["url"] = "synthetic://RESTRICTED_SERVICE_SENTINEL"
    if unusable == "expired":
        source["observed_at"] = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        source["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
    elif unusable == "unknown_rights":
        source["rights"]["display"] = "unknown"
    elif unusable == "future":
        source["observed_at"] = (
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).isoformat()
    else:
        source["expires_at"] = (
            datetime.fromisoformat(state["meal"]["meal_at"]) + timedelta(minutes=30)
        ).isoformat()
    raw["sources"].append(source)
    outlet = raw["outlets"][0]
    outlet["opening_exceptions_coverage"].update(
        starts_on=local_date, ends_on=local_date
    )
    if field == "opening_exceptions":
        exception(
            raw,
            local_date,
            "published",
            [
                {
                    "opens": "10:00",
                    "closes": "23:00",
                    "last_order": "21:30",
                    "last_order_source_ids": outlet["source_ids"],
                }
            ],
        )
        outlet[field][0]["source_ids"] = [source["source_id"]]
    elif field == "opening_exceptions_coverage":
        outlet[field]["source_ids"] = [source["source_id"]]
    else:
        for interval in outlet["opening_hours"]:
            interval["last_order_source_ids"] = [source["source_id"]]
    result = Recommender(Catalog.model_validate(raw))(state)
    assert not result["options"]
    assert result["status"] == "needs_verification"
    assert "RESTRICTED_SERVICE_SENTINEL" not in str(result)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_date",
        "closed_with_intervals",
        "published_without_intervals",
        "unknown_with_intervals",
        "reversed_coverage",
        "unknown_exception_source",
        "unknown_coverage_source",
        "unknown_kitchen_source",
        "last_order_after_close",
        "last_order_before_open",
        "last_order_without_source",
        "source_without_last_order",
    ],
)
def test_invalid_service_claims_rejected(mutation):
    raw = dated_catalog()
    outlet = raw["outlets"][0]
    exception(raw, "2026-10-06")
    interval = outlet["opening_hours"][0]
    if mutation == "duplicate_date":
        outlet["opening_exceptions"] *= 2
    elif mutation in {"closed_with_intervals", "unknown_with_intervals"}:
        outlet["opening_exceptions"][0]["status"] = mutation.split("_")[0]
        outlet["opening_exceptions"][0]["intervals"] = [
            {"opens": "10:00", "closes": "16:00"}
        ]
    elif mutation == "published_without_intervals":
        outlet["opening_exceptions"][0]["status"] = "published"
    elif mutation == "reversed_coverage":
        outlet["opening_exceptions_coverage"]["ends_on"] = "2026-09-30"
    elif mutation == "unknown_exception_source":
        outlet["opening_exceptions"][0]["source_ids"] = ["missing"]
    elif mutation == "unknown_coverage_source":
        outlet["opening_exceptions_coverage"]["source_ids"] = ["missing"]
    elif mutation == "unknown_kitchen_source":
        interval["last_order_source_ids"] = ["missing"]
    elif mutation == "last_order_after_close":
        interval["last_order"] = "23:30"
    elif mutation == "last_order_before_open":
        interval["last_order"] = "09:30"
    elif mutation == "last_order_without_source":
        interval["last_order_source_ids"] = []
    else:
        interval["last_order"] = None
    with pytest.raises(ValidationError):
        Catalog.model_validate(raw)


def test_legacy_catalog_remains_loadable_without_inventing_service_facts():
    raw = dated_catalog()
    outlet = raw["outlets"][0]
    outlet.pop("opening_exceptions_coverage")
    for interval in outlet["opening_hours"]:
        interval.pop("last_order")
        interval.pop("last_order_source_ids")
    parsed = Catalog.model_validate(raw).outlets[0]
    assert parsed.opening_exceptions_coverage is None
    assert not parsed.opening_exceptions
    assert parsed.opening_hours[0].last_order is None
