"""sync_ical: the iCal push target (events-and-sync.md §3, Step 11).

Publishes a UDM "event" entity's effective-values snapshot
(SyncBaseItem.synced_payload) as a single VEVENT into the target's local
.ics feed file, which is what gets served to external calendar subscribers.
No live remote push happens here (unlike sync_webhook) — the "remote" is the
generated .ics artifact on disk; other apps/urls serve that file.
"""
from __future__ import annotations

import os
import uuid
from typing import ClassVar

import icalendar
from django.conf import settings
from django.db import models
from django.utils.dateparse import parse_datetime

from sync_core.models import SyncBaseItem, SyncBaseTarget


def _parse_effective_datetime(value):
    """synced_payload is a JSONField snapshot, so datetimes round-trip as
    ISO-8601 strings; tolerate already-parsed datetime/date values too."""
    if not value:
        return None
    if isinstance(value, str):
        return parse_datetime(value) or value
    return value


def _ics_storage_path(target_key: str) -> str:
    directory = getattr(settings, "SYNC_ICAL_FEED_DIR", None) or os.path.join(
        getattr(settings, "MEDIA_ROOT", "/tmp"), "sync_ical_feeds",
    )
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, f"{target_key}.ics")


class IcalCalendarSyncTarget(SyncBaseTarget):
    """A published iCal feed built up from the pushed items that reference it."""

    description = models.TextField(blank=True, default="")

    def feed_path(self) -> str:
        return _ics_storage_path(self.key)

    def __str__(self):
        return self.name


class IcalCalendarSyncItem(SyncBaseItem):
    """One (entity, iCal target) sync relationship. `push()` (re)writes this
    item's VEVENT into the target's on-disk .ics feed, sourcing field values
    from `synced_payload` (the effective-values snapshot recorded by
    mark_sync) rather than any live model instance."""

    #: Fields whose values must never be exposed through the public API.
    secret_field_names: ClassVar[list[str]] = []

    def _build_vevent(self) -> icalendar.Event:
        payload = self.synced_payload or {}
        vevent = icalendar.Event()
        vevent.add("UID", self.remote_uid or str(self.related_entity_id))
        vevent.add("SUMMARY", payload.get("title") or "")
        start = _parse_effective_datetime(payload.get("start"))
        if start:
            vevent.add("DTSTART", start)
        end = _parse_effective_datetime(payload.get("end"))
        if end:
            vevent.add("DTEND", end)
        if payload.get("description"):
            vevent.add("DESCRIPTION", payload["description"])
        if payload.get("location"):
            vevent.add("LOCATION", payload["location"])
        return vevent

    def push(self) -> None:
        target = self.sync_target
        if not isinstance(target, IcalCalendarSyncTarget):
            target = IcalCalendarSyncTarget.objects.get(pk=target.pk)

        if not self.remote_uid:
            self.remote_uid = str(uuid.uuid4())
            self.save(update_fields=["remote_uid"])

        path = target.feed_path()
        calendar = icalendar.Calendar()
        if os.path.exists(path):
            with open(path, "rb") as fh:
                try:
                    calendar = icalendar.Calendar.from_ical(fh.read())
                except ValueError:
                    calendar = icalendar.Calendar()
        if not calendar.get("VERSION"):
            calendar.add("VERSION", "2.0")
        if not calendar.get("PRODID"):
            calendar.add("PRODID", "-//sync_ical//EN")

        # Drop any existing VEVENT for this item's UID, then append the fresh one.
        remaining = [
            component for component in calendar.subcomponents
            if not (component.name == "VEVENT" and str(component.get("UID")) == self.remote_uid)
        ]
        calendar.subcomponents = remaining
        calendar.add_component(self._build_vevent())

        with open(path, "wb") as fh:
            fh.write(calendar.to_ical())

    def __str__(self):
        return f"IcalCalendarSyncItem(entity={self.related_entity_id}, target={self.sync_target_id})"
