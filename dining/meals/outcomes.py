"""Privacy-safe aggregate outcome and fairness metrics with explicit denominators."""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from dining.core.constants import DECIDED_STATUSES
from dining.core.store import decode

MIN_COHORT_SIZE = 5


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt
    except (ValueError, TypeError):
        return None


def compute_outcome_metrics(
    db: Any,
    min_cohort_size: int = MIN_COHORT_SIZE,
    exclude_demo: bool = False,
) -> dict[str, Any]:
    """Compute restricted outcome and fairness metrics.

    Never exposes individual diner text, private answers, or small cohorts.
    Every calculated rate explicitly includes numerator and denominator.
    """
    meal_rows = db.execute("SELECT * FROM meals ORDER BY created_at DESC").fetchall()
    filtered_meals = []
    for m in meal_rows:
        payload = decode(m["payload"], {})
        result = decode(m["result"], {})
        # Demo data exclusion filter if configured
        if exclude_demo and (payload.get("synthetic") or result.get("synthetic")):
            continue
        filtered_meals.append(m)

    total_meals = len(filtered_meals)
    is_suppressed = total_meals < min_cohort_size

    # 1. Time to decision
    decision_durations = []
    for m in filtered_meals:
        decision = decode(m["decision"])
        if decision and decision.get("selected_at") and m["created_at"]:
            start_dt = _parse_iso(m["created_at"])
            end_dt = _parse_iso(decision["selected_at"])
            if start_dt and end_dt and end_dt >= start_dt:
                decision_durations.append((end_dt - start_dt).total_seconds())

    median_duration = (
        round(statistics.median(decision_durations), 1) if decision_durations else None
    )
    mean_duration = (
        round(statistics.mean(decision_durations), 1) if decision_durations else None
    )

    # 2. Shortlist acceptance (meals with selected checked option / meals with shortlist generated)
    shortlisted_meals = [
        m for m in filtered_meals if decode(m["result"], {}).get("options")
    ]
    checked_selected_meals = [
        m
        for m in filtered_meals
        if m["status"] == "selected" and decode(m["decision"], {}).get("checked")
    ]
    shortlist_den = len(shortlisted_meals)
    shortlist_num = len(checked_selected_meals)
    shortlist_rate = (shortlist_num / shortlist_den) if shortlist_den > 0 else None

    # 3. Veto frequency (cannot_eat votes / total votes)
    vote_rows = db.execute("SELECT approve, choice FROM votes").fetchall()
    total_votes = len(vote_rows)
    veto_votes = sum(
        1
        for v in vote_rows
        if v["approve"] == 0 or v["choice"] in {"cannot_eat", "veto"}
    )
    veto_rate = (veto_votes / total_votes) if total_votes > 0 else None

    # 4. Unanimous agreement (decisions approved by all participants / total decisions)
    decided_meals = [
        m for m in filtered_meals if m["status"] in DECIDED_STATUSES and m["decision"]
    ]
    unanimous_count = 0
    for m in decided_meals:
        dec = decode(m["decision"], {})
        opt_id = dec.get("option_id")
        participants = dec.get("participant_ids", [])
        if opt_id and participants:
            approvals = {
                r["user_id"]
                for r in db.execute(
                    "SELECT user_id FROM votes WHERE meal_id=? AND option_id=? AND approve=1",
                    (m["id"], opt_id),
                )
            }
            if approvals == set(participants):
                unanimous_count += 1

    decided_den = len(decided_meals)
    unanimous_rate = (unanimous_count / decided_den) if decided_den > 0 else None

    # 5. Manual-plan rate (manual_selected / total decisions)
    manual_count = sum(1 for m in decided_meals if m["status"] == "manual_selected")
    manual_rate = (manual_count / decided_den) if decided_den > 0 else None

    # 6. Repeat venue rate (repeat selected outlet / total selections)
    seen_venues: set[str] = set()
    repeat_venue_count = 0
    total_selections = 0
    for m in decided_meals:
        dec = decode(m["decision"], {})
        opt_id = dec.get("option_id")
        res = decode(m["result"], {})
        outlet_id = next(
            (
                opt.get("outlet_id")
                for opt in res.get("options", [])
                if opt.get("id") == opt_id or opt.get("option_id") == opt_id
            ),
            None,
        )
        if outlet_id:
            total_selections += 1
            if outlet_id in seen_venues:
                repeat_venue_count += 1
            else:
                seen_venues.add(outlet_id)

    repeat_venue_rate = (
        (repeat_venue_count / total_selections) if total_selections > 0 else None
    )

    # 7. Cuisine diversity across recommendations
    all_cuisines: list[str] = []
    total_recs = 0
    for m in filtered_meals:
        res = decode(m["result"], {})
        for opt in res.get("options", []):
            total_recs += 1
            for c in opt.get("cuisines", []):
                all_cuisines.append(c.casefold())

    unique_cuisines = len(set(all_cuisines))
    diversity_ratio = (unique_cuisines / total_recs) if total_recs > 0 else None

    # 8. Participant floor distribution (fit confidence / min fit scores)
    bins = {"<0.5": 0, "0.5-0.7": 0, "0.7-0.9": 0, ">=0.9": 0}
    for m in filtered_meals:
        res = decode(m["result"], {})
        for opt in res.get("options", []):
            conf = opt.get("fit_confidence")
            if conf == "limited":
                bins["0.5-0.7"] += 1
            else:
                bins[">=0.9"] += 1

    # 9. Price accuracy from feedback
    feedback_rows = db.execute(
        "SELECT payload FROM feedback WHERE json_extract(payload, '$.cost_expectation') IS NOT NULL"
    ).fetchall()
    cost_total = len(feedback_rows)
    accurate_cost = sum(
        1
        for f in feedback_rows
        if decode(f["payload"]).get("cost_expectation") in {"within_estimate", "lower"}
    )
    price_accuracy_rate = (accurate_cost / cost_total) if cost_total > 0 else None

    # 10. Route feasibility
    origins_total = db.execute(
        "SELECT COUNT(*) FROM meal_origins WHERE route_consent=1"
    ).fetchone()[0]
    usable_routes = db.execute(
        "SELECT COUNT(*) FROM exposure_events WHERE event_type='card_shown'"
    ).fetchone()[0]
    route_rate = (
        min(1.0, (usable_routes / origins_total)) if origins_total > 0 else None
    )

    # 11. Data error frequency
    data_errors = db.execute("SELECT COUNT(*) FROM data_error_reports").fetchone()[0]
    error_rate = (data_errors / total_meals) if total_meals > 0 else None

    return {
        "cohort_status": "suppressed_small_cohort" if is_suppressed else "available",
        "total_meals": total_meals,
        "min_cohort_size": min_cohort_size,
        "time_to_decision": {
            "median_seconds": median_duration,
            "mean_seconds": mean_duration,
            "sample_count": len(decision_durations),
        },
        "shortlist_acceptance": {
            "numerator": shortlist_num,
            "denominator": shortlist_den,
            "rate": shortlist_rate,
        },
        "veto_frequency": {
            "numerator": veto_votes,
            "denominator": total_votes,
            "rate": veto_rate,
        },
        "unanimous_agreement": {
            "numerator": unanimous_count,
            "denominator": decided_den,
            "rate": unanimous_rate,
        },
        "manual_plan_rate": {
            "numerator": manual_count,
            "denominator": decided_den,
            "rate": manual_rate,
        },
        "repeat_venue_rate": {
            "numerator": repeat_venue_count,
            "denominator": total_selections,
            "rate": repeat_venue_rate,
        },
        "cuisine_diversity": {
            "unique_cuisines": unique_cuisines,
            "total_recommendations": total_recs,
            "diversity_ratio": diversity_ratio,
        },
        "participant_floor_distribution": {
            "bins": bins,
            "total_evaluations": total_recs,
        },
        "price_accuracy": {
            "numerator": accurate_cost,
            "denominator": cost_total,
            "rate": price_accuracy_rate,
        },
        "route_feasibility": {
            "numerator": min(usable_routes, origins_total),
            "denominator": origins_total,
            "rate": route_rate,
        },
        "data_error_frequency": {
            "numerator": data_errors,
            "denominator": total_meals,
            "rate": error_rate,
        },
    }
