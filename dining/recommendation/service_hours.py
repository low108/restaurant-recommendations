"""Opening-hours and kitchen-service checks for a planned visit.

A visit passes only when the outlet is known to be open for the whole meal *and* still
taking orders at arrival. Three answers are possible:

* ``True``  – published hours, dated-exception coverage and last-order evidence all agree.
* ``False`` – a known closure: a dated "closed" exception, or no published weekly interval
  covers the visit at all.
* ``None``  – unknown: some evidence is missing or stale, so the caller decides whether that
  is a trade-off (ordinary meals) or a blocker (allergy / certified-halal sessions).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as local_time
from zoneinfo import ZoneInfo

from dining.catalog.models import Catalog, Outlet


@dataclass(frozen=True)
class ServiceCheck:
    """Result of a service-hours check for one outlet and one visit window."""

    passes: bool | None
    source_ids: tuple[str, ...] = ()
    # Published weekly hours cover the visit, but dated exceptions are not established.
    weekly_open: bool = False


def _interval_window(on: date, interval, tz) -> tuple[datetime, datetime]:
    """Concrete start/finish datetimes for a weekly or exception interval on a date."""
    start = datetime.combine(on, local_time.fromisoformat(interval.opens), tzinfo=tz)
    finish = datetime.combine(on, local_time.fromisoformat(interval.closes), tzinfo=tz)
    if interval.closes_next_day:
        finish += timedelta(days=1)
    return start, finish


def weekly_service(
    outlet: Outlet, local: datetime, end: datetime, exceptions
) -> bool | None:
    """Published weekly hours only: True covers the visit, False is a known-closed slot.

    Used to exclude outlets that are closed by their own published schedule even when
    dated exception coverage is missing. A dated exception owns its date, so any visit
    date with an exception defers to the full check (returns None).
    """
    if outlet.hours_status != "published" or not outlet.opening_hours:
        return None
    day = local.date()
    last = (end - timedelta(microseconds=1)).date()
    if any(d in exceptions for d in (day - timedelta(days=1), day, last)):
        return None
    # Yesterday's interval may run past midnight (e.g. 18:00–03:00), so check both days.
    for offset in (-1, 0):
        on = day + timedelta(days=offset)
        for interval in outlet.opening_hours:
            if interval.weekday != on.weekday():
                continue
            start, finish = _interval_window(on, interval, local.tzinfo)
            if start <= local and end <= finish:
                return True
    return False


def service_for(
    outlet: Outlet,
    at: datetime,
    duration: int,
    *,
    catalog: Catalog | None = None,
    checked_at: datetime | None = None,
) -> ServiceCheck:
    """Check arrival, kitchen deadline and uninterrupted meal time in local dates.

    Dated exceptions replace the entire local date, including prior-night spillover.
    A positive result needs dated exception coverage and explicit last-order evidence;
    published weekly hours alone can only *rule out* a visit. Without a catalog this
    checks structure/time only (useful for fixture tests).
    """
    if at.tzinfo is None or duration <= 0:
        return ServiceCheck(None)
    local = at.astimezone(ZoneInfo(outlet.timezone))
    end = local + timedelta(minutes=duration)
    now = checked_at or datetime.now(timezone.utc)
    exceptions = {entry.on_date: entry for entry in outlet.opening_exceptions}

    weekly = weekly_service(outlet, local, end, exceptions)
    if weekly is False:
        return ServiceCheck(False)

    def usable(ids) -> bool:
        """Evidence must be current both now and when the meal ends."""
        return bool(ids) and (
            catalog is None
            or (
                catalog.evidence_usable(ids, at=now)
                and catalog.evidence_usable(ids, at=end)
            )
        )

    def day_schedule(day: date):
        """(intervals, source_ids) that apply on a date, or None when unknown."""
        exception = exceptions.get(day)
        if exception is not None:
            if exception.status == "unknown" or not usable(exception.source_ids):
                return None
            return exception.intervals, exception.source_ids
        coverage = outlet.opening_exceptions_coverage
        if (
            outlet.hours_status != "published"
            or not usable(outlet.source_ids)
            or coverage is None
            or not coverage.starts_on <= day <= coverage.ends_on
            or not usable(coverage.source_ids)
        ):
            return None
        return (
            tuple(i for i in outlet.opening_hours if i.weekday == day.weekday()),
            tuple(set(outlet.source_ids) | set(coverage.source_ids)),
        )

    # Every local date the meal occupies. A meal ending exactly at midnight does not
    # occupy the following date.
    visit_dates = []
    day = local.date()
    last_date = (end - timedelta(microseconds=1)).date()
    while day <= last_date:
        visit_dates.append(day)
        day += timedelta(days=1)
    schedules = {day: day_schedule(day) for day in visit_dates}

    # A dated "closed" exception on any visit date is a known closure.
    for day, schedule in schedules.items():
        exception = exceptions.get(day)
        if schedule is not None and exception and exception.status == "closed":
            return ServiceCheck(False)

    uncertain = any(schedule is None for schedule in schedules.values())
    date_sources = {
        sid for schedule in schedules.values() if schedule for sid in schedule[1]
    }

    for offset in (-1, 0):
        day = (local + timedelta(days=offset)).date()
        schedule = day_schedule(day)
        if schedule is None:
            uncertain = True
            continue
        intervals, source_ids = schedule
        for interval in intervals:
            start, finish = _interval_window(day, interval, local.tzinfo)
            next_day = day + timedelta(days=1)
            if next_day in exceptions:
                # An exception owns its whole date; never inherit yesterday's hours.
                finish = min(
                    finish,
                    datetime.combine(next_day, local_time.min, tzinfo=local.tzinfo),
                )
            if not (start <= local and end <= finish):
                continue
            # Open for the whole meal; now the kitchen must still be taking orders.
            if interval.last_order is None or not usable(
                interval.last_order_source_ids
            ):
                uncertain = True
                continue
            last_order = datetime.combine(
                day, local_time.fromisoformat(interval.last_order), tzinfo=local.tzinfo
            ) + timedelta(days=int(interval.last_order_next_day))
            if local > last_order:
                continue
            if all(schedule is not None for schedule in schedules.values()):
                return ServiceCheck(
                    True,
                    tuple(
                        sorted(
                            date_sources
                            | set(source_ids)
                            | set(interval.last_order_source_ids)
                        )
                    ),
                )
    return ServiceCheck(None if uncertain else False, weekly_open=bool(weekly))


def open_for(outlet: Outlet, at: datetime, duration: int) -> bool | None:
    """Convenience wrapper returning only the pass/fail/unknown answer."""
    return service_for(outlet, at, duration).passes
