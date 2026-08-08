"""Pull-side fetch/parse for CalendarSource (§6/Step 9).

Lifted from sync_ical.tasks.parse_calendar_data / sync_caldav.tasks._expand_calendar
(the fetch/parse half only — none of the Event-creation/push logic, which stays
in those apps pending the deferred Step 6 port). Each function returns plain
occurrence dicts; fetch_calendar_source() in models.py does the upsert.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import icalendar
import recurring_ical_events
import requests
from django.utils import timezone as django_timezone

logger = logging.getLogger(__name__)


def _default_window() -> tuple[date, date]:
    today = django_timezone.now().date()
    return today - timedelta(days=365), today + timedelta(days=366)


def _as_utc_datetime(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _read_text(component: icalendar.cal.Component, key: str) -> Optional[str]:
    value = component.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_all_day(component: icalendar.cal.Component) -> bool:
    dtstart = component.get("DTSTART")
    return bool(dtstart) and not isinstance(dtstart.dt, datetime)


def _parse_occurrences(ics_content: str) -> list[dict]:
    window_start, window_end = _default_window()
    calendar = icalendar.Calendar.from_ical(ics_content)
    expanded = recurring_ical_events.of(calendar).between(window_start, window_end)

    occurrences: list[dict] = []
    seen: set[tuple] = set()
    for component in expanded:
        uid = _read_text(component, "UID")
        if not uid or component.get("DTSTART") is None:
            continue
        dtstart = _as_utc_datetime(component.decoded("DTSTART"))
        if component.get("DTEND") is not None:
            dtend = _as_utc_datetime(component.decoded("DTEND"))
        elif component.get("DURATION") is not None:
            dtend = dtstart + component.decoded("DURATION")
        else:
            dtend = dtstart

        key = (uid, dtstart, dtend)
        if key in seen:
            continue
        seen.add(key)
        occurrences.append({
            "uid": f"{uid}_{dtstart.isoformat()}",
            "title": _read_text(component, "SUMMARY") or "(no title)",
            "description": _read_text(component, "DESCRIPTION") or "",
            "start": dtstart,
            "end": dtend,
            "all_day": _is_all_day(component),
        })
    return occurrences


def fetch_ical_occurrences(source) -> list[dict]:
    """Fetch+parse an iCal feed (source.kind == KIND_ICAL, source.url)."""
    response = requests.get(source.url, timeout=30)
    response.raise_for_status()
    return _parse_occurrences(response.text)


def fetch_caldav_occurrences(source) -> list[dict]:
    """Fetch+parse a CalDAV calendar (source.kind == KIND_CALDAV)."""
    from caldav.davclient import DAVClient

    client = DAVClient(url=source.url, username=source.username, password=source.password)
    principal = client.principal()
    calendars = principal.get_calendars()
    calendar = next(
        (cal for cal in calendars if cal.get_display_name() == source.calendar_display_name),
        None,
    )
    if calendar is None:
        raise ValueError(
            f"Calendar {source.calendar_display_name!r} not found on CalDAV server at {source.url}"
        )

    occurrences: list[dict] = []
    for cal_event in calendar.events():
        raw = cal_event.data if isinstance(cal_event.data, str) else cal_event.data.decode("utf-8")
        try:
            occurrences.extend(_parse_occurrences(raw))
        except Exception:
            logger.exception("Failed to parse CalDAV event for source %s, skipping", source.key)
    return occurrences
