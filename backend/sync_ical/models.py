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

    @classmethod
    def sync_item_model(cls):
        return IcalCalendarSyncItem


class IcalCalendarSyncItem(SyncBaseItem):
    """One (entity, iCal target) sync relationship. `push()` (re)writes this
    item's VEVENT into the target's on-disk .ics feed, sourcing field values
    from `synced_payload` (the effective-values snapshot recorded by
    mark_sync) rather than any live model instance."""

    #: Fields whose values must never be exposed through the public API.
    secret_field_names: ClassVar[list[str]] = []

    def _build_vevent(self, *, uid: str, start_raw, end_raw) -> icalendar.Event:
        # events-and-sync.md Step 13.2: a type with a `sync_ical` binding
        # config resolves remote properties (SUMMARY/LOCATION/DESCRIPTION/
        # DTSTART/DTEND); a type without one still stores the raw effective
        # dict under the legacy title/location/description/start/end keys —
        # support both.
        payload = self.synced_payload or {}
        vevent = icalendar.Event()
        vevent.add("UID", uid)
        vevent.add("SUMMARY", payload.get("SUMMARY", payload.get("title")) or "")
        start = _parse_effective_datetime(start_raw)
        if start:
            vevent.add("DTSTART", start)
        end = _parse_effective_datetime(end_raw)
        if end:
            vevent.add("DTEND", end)
        description = payload.get("DESCRIPTION", payload.get("description"))
        if description:
            vevent.add("DESCRIPTION", description)
        location = payload.get("LOCATION", payload.get("location"))
        if location:
            vevent.add("LOCATION", location)
        return vevent

    def _build_vevents(self) -> list[icalendar.Event]:
        """events-and-sync.md §13.3: fan out to one VEVENT per submodel
        child when the type's `sync_ical` tab config binds one (resolved
        into `payload["submodel"]` — a list of `{child_id, start, end}` —
        by `sync_core.binding.resolve_deep`/`resolve_submodel_slots`);
        otherwise the single legacy whole-entity VEVENT, unchanged."""
        payload = self.synced_payload or {}
        slots = payload.get("submodel")
        if slots:
            return [
                self._build_vevent(
                    uid=f"{self.related_entity_id}-{slot['child_id']}",
                    start_raw=slot.get("start"), end_raw=slot.get("end"),
                )
                for slot in slots
            ]
        return [self._build_vevent(
            uid=self.remote_uid or str(self.related_entity_id),
            start_raw=payload.get("DTSTART", payload.get("start")),
            end_raw=payload.get("DTEND", payload.get("end")),
        )]

    def push(self) -> None:
        target = self.sync_target
        if not isinstance(target, IcalCalendarSyncTarget):
            target = IcalCalendarSyncTarget.objects.get(pk=target.pk)

        payload = self.synced_payload or {}
        fan_out = bool(payload.get("submodel"))
        if not fan_out and not self.remote_uid:
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

        # Drop every existing VEVENT for this item — its legacy single UID,
        # or (fan-out) any UID prefixed by the entity id — then append the
        # fresh set. Re-adding the current resolution each push is what
        # deletes a remote VEVENT for a since-removed timeslot: it's simply
        # not in the fresh set, so it isn't re-added (§13.3).
        prefix = f"{self.related_entity_id}-"
        remaining = [
            component for component in calendar.subcomponents
            if not (
                component.name == "VEVENT"
                and (
                    (self.remote_uid and str(component.get("UID")) == self.remote_uid)
                    or str(component.get("UID")).startswith(prefix)
                )
            )
        ]
        calendar.subcomponents = remaining
        for vevent in self._build_vevents():
            calendar.add_component(vevent)

        with open(path, "wb") as fh:
            fh.write(calendar.to_ical())

    def __str__(self):
        return f"IcalCalendarSyncItem(entity={self.related_entity_id}, target={self.sync_target_id})"
