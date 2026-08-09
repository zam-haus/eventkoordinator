"""sync_caldav: pushes UDM entities out to a remote CalDAV calendar as VEVENTs
(events-and-sync.md §3, Step 11).

This is the *push* side only. The read/pull side that imports external
iCal/CalDAV calendars for the calendar widget lives in
`sync_core.calendar_fetch` and is unrelated to this app.

Push reads the "effective values" snapshot from `SyncBaseItem.synced_payload`
(set by `sync_core.models.mark_sync` — "the worker pushes exactly what was
current when mark_sync fired", events-and-sync.md §4.2) and treats it as an
arbitrary dict with (optional) `title`, `start`, `end`, `location` keys —
there is no dedicated "event" UDM type wired in yet.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import ClassVar
from uuid import uuid4

from caldav.davclient import DAVClient
from django.db import models
from django.utils import timezone

from sync_core.models import SyncBaseItem, SyncBaseTarget

logger = logging.getLogger(__name__)


class CalDAVSyncTarget(SyncBaseTarget):
    """A remote CalDAV calendar identified by its display name on the server."""

    #: Fields whose values must never be exposed through the public API.
    secret_field_names: ClassVar[list[str]] = ["password"]

    url = models.URLField(max_length=2000)
    username = models.CharField(max_length=255)
    password = models.CharField(max_length=255)
    calendar_display_name = models.CharField(max_length=255)
    instance_base_url = models.CharField(max_length=2000, blank=True, default="")

    def _get_calendar(self):
        logger.debug("Connecting to CalDAV server %s as %s", self.url, self.username)
        client = DAVClient(url=self.url, username=self.username, password=self.password)
        principal = client.principal()
        calendars = principal.get_calendars()
        logger.debug("Found %d calendars on server", len(calendars))
        for cal in calendars:
            display_name = cal.get_display_name()
            logger.debug("Checking calendar %r", display_name)
            if display_name == self.calendar_display_name:
                logger.debug("Matched calendar %r", display_name)
                return cal
        raise ValueError(
            f"Calendar {self.calendar_display_name!r} not found on CalDAV server at {self.url}"
        )


class CalDAVSyncItem(SyncBaseItem):
    """One (entity, CalDAV target) sync relationship. `remote_uid` (base field)
    holds the VEVENT UID currently believed to exist on the remote.

    `sync_target` is inherited as-is from `SyncBaseItem` (a plain FK to the
    polymorphic `SyncBaseTarget` base) — redeclaring it here as a FK to
    `CalDAVSyncTarget` would clash with the concrete parent field under
    Django's multi-table inheritance, so `_resolved_target()` downcasts it
    instead."""

    def _resolved_target(self) -> CalDAVSyncTarget:
        target = self.sync_target
        if not isinstance(target, CalDAVSyncTarget):
            target = CalDAVSyncTarget.objects.get(pk=target.pk)
        return target

    @staticmethod
    def _parse_datetime(value) -> datetime | None:
        """`synced_payload` is a JSON snapshot, so datetimes normally arrive as
        ISO-8601 strings; tolerate already-parsed datetimes too. Anything
        unparseable/missing resolves to None so the caller can default it."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            logger.debug("sync_caldav: could not parse datetime from %r", value)
            return None

    def push(self) -> None:
        effective = self.synced_payload or {}
        target = self._resolved_target()

        title = effective.get("title") or "(untitled event)"
        location = effective.get("location") or ""
        dtstart = self._parse_datetime(effective.get("start")) or timezone.now()
        dtend = self._parse_datetime(effective.get("end")) or dtstart

        try:
            calendar = target._get_calendar()
        except Exception as exc:
            raise RuntimeError(
                f"sync_caldav push: could not reach calendar {target.calendar_display_name!r}: {exc}"
            ) from exc

        # Delete the existing remote event if we have a UID for it — this avoids
        # conflicts with CalDAV servers that keep deleted events in a trash bin
        # under the same UID.
        if self.remote_uid:
            try:
                calendar.get_event_by_uid(self.remote_uid).delete()
                logger.debug("Deleted existing remote event uid=%s", self.remote_uid)
            except Exception:
                logger.debug("Remote event uid=%s already absent", self.remote_uid)

        # Assign a new UID and persist it as a checkpoint before touching the
        # remote — if the process dies here, the item stays pending with a
        # known (currently-nonexistent) UID, and the next push will simply
        # find nothing to delete and create fresh.
        new_uid = str(uuid4())
        self.remote_uid = new_uid
        self.save(update_fields=["remote_uid"])

        extra_props = {"x-eventkoordinator-entity": str(self.related_entity_id)}
        if target.instance_base_url:
            extra_props["x-eventkoordinator-instance"] = target.instance_base_url

        logger.debug(
            "Creating remote event uid=%s summary=%r start=%s end=%s",
            new_uid, title, dtstart, dtend,
        )
        try:
            calendar.add_event(
                dtstart=dtstart,
                dtend=dtend,
                uid=new_uid,
                summary=title,
                location=location,
                description="Created automatically. Do not edit, updates will be overwritten!",
                **extra_props,
            )
        except Exception as exc:
            raise RuntimeError(f"sync_caldav push: failed to create remote VEVENT: {exc}") from exc

        logger.debug("push complete for uid=%s", new_uid)

    def __str__(self):
        return f"CalDAVSyncItem(entity={self.related_entity_id}, target={self.sync_target_id}, uid={self.remote_uid})"
