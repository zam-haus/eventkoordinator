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

    @classmethod
    def sync_item_model(cls):
        return CalDAVSyncItem


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
        payload = self.synced_payload or {}
        target = self._resolved_target()

        try:
            calendar = target._get_calendar()
        except Exception as exc:
            raise RuntimeError(
                f"sync_caldav push: could not reach calendar {target.calendar_display_name!r}: {exc}"
            ) from exc

        extra_props = {"x-eventkoordinator-entity": str(self.related_entity_id)}
        if target.instance_base_url:
            extra_props["x-eventkoordinator-instance"] = target.instance_base_url

        if payload.get("submodel"):
            self._push_fan_out(calendar, payload, extra_props)
        else:
            self._push_single(calendar, payload, extra_props)

    def _push_single(self, calendar, payload: dict, extra_props: dict) -> None:
        """The original, unchanged whole-entity path: one VEVENT, a fresh
        UID every push (delete-old-then-recreate, not update-in-place)."""
        # events-and-sync.md Step 13.2: a type with a `sync_caldav` binding
        # config resolves remote properties (SUMMARY/LOCATION/DTSTART/DTEND);
        # a type without one still stores the raw effective dict under the
        # legacy title/location/start/end keys — support both.
        title = payload.get("SUMMARY", payload.get("title")) or "(untitled event)"
        location = payload.get("LOCATION", payload.get("location")) or ""
        dtstart = self._parse_datetime(payload.get("DTSTART", payload.get("start"))) or timezone.now()
        dtend = self._parse_datetime(payload.get("DTEND", payload.get("end"))) or dtstart
        description = payload.get("DESCRIPTION") or "Created automatically. Do not edit, updates will be overwritten!"

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
                description=description,
                **extra_props,
            )
        except Exception as exc:
            raise RuntimeError(f"sync_caldav push: failed to create remote VEVENT: {exc}") from exc

        logger.debug("push complete for uid=%s", new_uid)

    def _push_fan_out(self, calendar, payload: dict, extra_props: dict) -> None:
        """events-and-sync.md §13.3: one VEVENT per `payload["submodel"]`
        slot (resolved by `sync_core.binding.resolve_submodel_slots`),
        each keyed by a **stable** `entity_id-child_id` uid — moving a slot
        updates its VEVENT (delete-then-recreate under the same uid, same
        as the single-VEVENT path's per-push semantics) rather than
        creating a new one; a since-removed slot's VEVENT is deleted and
        never recreated. `self.remote_uid` is not used in this mode — the
        (entity, target) row stays singular, only the payload/remote state
        becomes a list."""
        title = payload.get("SUMMARY", payload.get("title")) or "(untitled event)"
        location = payload.get("LOCATION", payload.get("location")) or ""
        description = payload.get("DESCRIPTION") or "Created automatically. Do not edit, updates will be overwritten!"
        slots = payload.get("submodel") or []
        prefix = f"{self.related_entity_id}-"
        new_uids = {f"{prefix}{slot['child_id']}" for slot in slots}

        try:
            existing = calendar.events()
        except Exception as exc:
            logger.debug("sync_caldav: could not list existing events (%s) — skipping stale-slot cleanup.", exc)
            existing = []
        for event in existing:
            uid = getattr(event, "id", None)
            if uid and uid.startswith(prefix) and uid not in new_uids:
                try:
                    event.delete()
                    logger.debug("Deleted remote event for removed timeslot uid=%s", uid)
                except Exception:
                    logger.debug("Remote event uid=%s already absent", uid)

        for slot in slots:
            uid = f"{prefix}{slot['child_id']}"
            try:
                calendar.get_event_by_uid(uid).delete()
                logger.debug("Deleted existing remote event uid=%s", uid)
            except Exception:
                logger.debug("Remote event uid=%s already absent", uid)

            dtstart = self._parse_datetime(slot.get("start")) or timezone.now()
            dtend = self._parse_datetime(slot.get("end")) or dtstart
            logger.debug(
                "Creating remote event uid=%s summary=%r start=%s end=%s",
                uid, title, dtstart, dtend,
            )
            try:
                calendar.add_event(
                    dtstart=dtstart,
                    dtend=dtend,
                    uid=uid,
                    summary=title,
                    location=location,
                    description=description,
                    **extra_props,
                )
            except Exception as exc:
                raise RuntimeError(f"sync_caldav push: failed to create remote VEVENT: {exc}") from exc

        logger.debug("fan-out push complete for uids=%s", new_uids)

    def __str__(self):
        return f"CalDAVSyncItem(entity={self.related_entity_id}, target={self.sync_target_id}, uid={self.remote_uid})"
