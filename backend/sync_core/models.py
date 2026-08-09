"""sync_core: the relocated, UDM-targeted sync framework (events-and-sync.md §3).

Dependency direction is one-way: sync_core -> userdefinedmodel. Nothing in
userdefinedmodel imports sync_core at module load time (only lazily, inside
functions) so the apps stay decoupled.
"""
from __future__ import annotations

from typing import ClassVar

from django.db import models

from project.basemodels import PolymorphicMetaBase

#: derived_state values (§3.2) — computed once, consumed identically by
#: rego (input.sync), templates (sync), and the API (sync_items).
DERIVED_STATE_PENDING = "pending"
DERIVED_STATE_ERROR = "error"
DERIVED_STATE_SYNCED = "synced"
DERIVED_STATE_STALE = "stale"
DERIVED_STATE_TARGET_UNAVAILABLE = "target_unavailable"


class SyncBaseTarget(PolymorphicMetaBase):
    """A remote destination (CalDAV calendar, ticketing system, webhook, …).

    Soft-deleted via `enabled`, never hard-deleted while items reference it —
    SyncBaseItem.sync_target must not cascade away the evidence (§3.2).
    """

    #: Fields whose values must never be exposed through the public API.
    secret_field_names: ClassVar[list[str]] = []

    key = models.SlugField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        if self.items.exists():
            raise models.ProtectedError(
                f"Cannot hard-delete sync target {self.key!r} while items reference it; "
                "disable it instead (enabled=False).",
                self.items.all(),
            )
        return super().delete(*args, **kwargs)


class SyncBaseItem(PolymorphicMetaBase):
    """One (entity, target) sync relationship. `status` is a plain CharField
    validated against the concrete item class's `allowed_statuses()` — not a
    global choices enum, since subclasses may add statuses (§3.1)."""

    #: Base statuses every item class supports; concrete subclasses may add
    #: more (e.g. a ticketing item adding "cancelled").
    BASE_STATUSES: ClassVar[frozenset[str]] = frozenset({
        DERIVED_STATE_PENDING, DERIVED_STATE_SYNCED, DERIVED_STATE_ERROR,
    })

    related_entity = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelEntity",
        on_delete=models.CASCADE,
        related_name="sync_items",
    )
    sync_target = models.ForeignKey(SyncBaseTarget, on_delete=models.PROTECT, related_name="items")
    status = models.CharField(max_length=30, default=DERIVED_STATE_PENDING)
    last_error = models.TextField(blank=True, default="")
    #: Snapshot of the effective values as of the last mark_sync (§4.2) —
    #: exactly what was/will be pushed. Never re-derived at push time.
    synced_payload = models.JSONField(null=True, blank=True)
    #: Set by the post-save staleness check (§3.2/§4.3): current effective
    #: values differ from synced_payload and nothing is pending.
    is_stale = models.BooleanField(default=False)
    synced_at = models.DateTimeField(null=True, blank=True)
    remote_uid = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["related_entity", "sync_target"], name="unique_sync_item_per_entity_target",
            ),
        ]

    @classmethod
    def allowed_statuses(cls) -> frozenset[str]:
        return cls.BASE_STATUSES

    def derived_state(self) -> str:
        return compute_derived_state(self)

    def push(self) -> None:
        """Push synced_payload to the remote target. Concrete item classes
        (a ported sync_ical/sync_caldav/sync_pretix or sync_webhook, none of
        which exist yet) override this; the base implementation makes the
        worker's contract explicit rather than silently doing nothing."""
        raise NotImplementedError(
            f"{type(self).__name__} does not implement push() — no concrete "
            "sync target plugin is wired up yet (events-and-sync.md Step 6)."
        )

    def __str__(self):
        return f"SyncBaseItem(entity={self.related_entity_id}, target={self.sync_target_id}, status={self.status})"


def _target_bound_to_entity_type(target_key: str, entity_id) -> bool:
    """Is `target_key` listed in the entity's config-version `sync_targets`
    tab config? Absence of a tab config row at all is treated as
    "no restriction configured" (backward compatible with types that never
    set up the tab) — only a tab config that exists and omits the key counts
    as "no longer bound" (events-and-sync.md Step 11)."""
    from userdefinedmodel.models import TypeEditorTabConfig, UserDefinedModelEntity

    try:
        config_version_id = UserDefinedModelEntity.objects.values_list(
            "config_version_id", flat=True,
        ).get(pk=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return True
    cfg = TypeEditorTabConfig.objects.filter(
        config_version_id=config_version_id, tab_id="sync_targets",
    ).first()
    if cfg is None:
        return True
    return target_key in (cfg.config.get("target_keys") or [])


def compute_derived_state(item: SyncBaseItem) -> str:
    """The single shared derived_state computation (§3.2) — every surface
    (rego input.sync, templates, the sync_status element) reads this, never
    re-derives it independently."""
    target = item.sync_target
    if target is None or not target.enabled:
        return DERIVED_STATE_TARGET_UNAVAILABLE
    if item.sync_target_id and not _target_bound_to_entity_type(target.key, item.related_entity_id):
        return DERIVED_STATE_TARGET_UNAVAILABLE
    if item.status == DERIVED_STATE_ERROR:
        return DERIVED_STATE_ERROR
    if item.status == DERIVED_STATE_PENDING:
        return DERIVED_STATE_PENDING
    if item.status == DERIVED_STATE_SYNCED:
        return DERIVED_STATE_STALE if item.is_stale else DERIVED_STATE_SYNCED
    # A subclass-defined status (e.g. "cancelled") outside the base surfaces
    # is reported as-is; only the base three participate in stale/unavailable
    # derivation.
    return item.status


def sync_item_summary(item: SyncBaseItem) -> dict:
    """The shape shared by input.sync, the `sync` template context, and
    EntityOut.sync_items (§3.1/§3.2)."""
    return {
        "target": item.sync_target.key if item.sync_target_id else None,
        "status": item.status,
        "derived_state": item.derived_state(),
        "last_error": item.last_error,
        "synced_at": item.synced_at.isoformat() if item.synced_at else None,
        "remote_uid": item.remote_uid or None,
    }


def mark_sync(entity_id, target_key: str, status: str, *, effective: dict | None = None) -> SyncBaseItem:
    """Create/flip the (entity, target) item to `status` (§4.1).

    When `status` implies a push (pending), the caller's `effective` snapshot
    is stored verbatim in synced_payload — "the worker pushes exactly what
    was current when mark_sync fired" (§4.2), never re-evaluated later.
    Raises ValueError for an unknown/disabled target or a status the item
    class does not allow from policy.
    """
    try:
        target = SyncBaseTarget.objects.get(key=target_key)
    except SyncBaseTarget.DoesNotExist:
        raise ValueError(f"mark_sync: unknown target {target_key!r}")
    if not target.enabled:
        raise ValueError(f"mark_sync: target {target_key!r} is disabled")
    if not _target_bound_to_entity_type(target_key, entity_id):
        raise ValueError(
            f"mark_sync: target {target_key!r} is not bound to this entity's type "
            "(sync_targets tab config)"
        )

    # No concrete SyncBaseItem subclass exists yet (Step 6 port is deferred),
    # so the base class is always the real instance; once a plugin lands,
    # existing rows resolve polymorphically via SyncBaseItem.objects here too.
    item, _ = SyncBaseItem.objects.get_or_create(
        related_entity_id=entity_id, sync_target=target, defaults={"status": status},
    )
    real = item.get_real_instance()
    if status not in real.allowed_statuses():
        raise ValueError(
            f"mark_sync: status {status!r} is not allowed by {type(real).__name__} "
            f"(allowed: {sorted(real.allowed_statuses())})"
        )
    real.status = status
    if status == DERIVED_STATE_PENDING:
        real.synced_payload = effective if effective is not None else {}
        real.is_stale = False
    real.save()
    return real


def recompute_staleness(entity_id, effective: dict) -> None:
    """Post-save staleness check (§3.2/§4.3): compare the fresh `effective`
    against each SYNCED item's synced_payload, no input.changed_fields
    involved (deliberate — see events-and-sync.md §4.3)."""
    items = SyncBaseItem.objects.filter(related_entity_id=entity_id, status=DERIVED_STATE_SYNCED)
    for item in items:
        stale = item.synced_payload != effective
        if stale != item.is_stale:
            item.is_stale = stale
            item.save(update_fields=["is_stale"])


def sync_map_for_entity(entity_id) -> dict[str, dict]:
    """{target_key: summary} for every sync item on entity_id (§3.2)."""
    items = (
        SyncBaseItem.objects.filter(related_entity_id=entity_id)
        .select_related("sync_target")
    )
    result = {}
    for item in items:
        if item.sync_target_id is None:
            continue
        result[item.sync_target.key] = sync_item_summary(item)
    return result


class CalendarSource(PolymorphicMetaBase):
    """A read-only remote calendar pulled into local RemoteCalendarEntry rows
    for the calendar view to query (§6/Step 9).

    This is the *pull* side — reading busy blocks from an iCal feed or CalDAV
    calendar — and is deliberately separate from SyncBaseTarget/SyncBaseItem
    (the *push* side, ported from sync_ical/sync_caldav in Step 6). The same
    remote calendar could in principle be configured on both independently.
    """

    KIND_ICAL = "ical"
    KIND_CALDAV = "caldav"
    KIND_CHOICES = [(KIND_ICAL, "iCal feed"), (KIND_CALDAV, "CalDAV calendar")]

    #: Fields whose values must never be exposed through the public API.
    secret_field_names: ClassVar[list[str]] = ["password"]

    key = models.SlugField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    url = models.URLField(max_length=2000)
    username = models.CharField(max_length=200, blank=True, default="")
    password = models.CharField(max_length=200, blank=True, default="")
    calendar_display_name = models.CharField(max_length=200, blank=True, default="")
    enabled = models.BooleanField(default=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    def __str__(self):
        return self.name


class RemoteCalendarEntry(PolymorphicMetaBase):
    """One fetched occurrence from a CalendarSource. Upserted by (source, uid)
    on each fetch; the request path (GET /calendar/) only ever reads these —
    no live remote fetch happens inline with a request (§6)."""

    source = models.ForeignKey(CalendarSource, on_delete=models.CASCADE, related_name="entries")
    uid = models.CharField(max_length=500)
    title = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    start = models.DateTimeField()
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "uid"], name="unique_remote_calendar_entry_per_source"),
        ]

    def __str__(self):
        return f"{self.title} ({self.source.key})"


def fetch_calendar_source(source_id) -> dict:
    """Fetch+parse a CalendarSource and upsert its RemoteCalendarEntry rows
    (§6). Plain function (see push_pending_sync_items convention) so it can
    be called synchronously from tests/management commands without a broker.
    Entries no longer present on the remote are deleted. One attempt — on any
    fetch/parse error, records source.last_error and leaves existing entries
    untouched.
    """
    from django.utils.timezone import now

    try:
        source = CalendarSource.objects.get(pk=source_id)
    except CalendarSource.DoesNotExist:
        raise ValueError(f"fetch_calendar_source: unknown source {source_id!r}")

    if source.kind == CalendarSource.KIND_ICAL:
        from sync_core.calendar_fetch import fetch_ical_occurrences
        fetch_fn = fetch_ical_occurrences
    elif source.kind == CalendarSource.KIND_CALDAV:
        from sync_core.calendar_fetch import fetch_caldav_occurrences
        fetch_fn = fetch_caldav_occurrences
    else:
        raise ValueError(f"fetch_calendar_source: unknown kind {source.kind!r}")

    try:
        occurrences = fetch_fn(source)
    except Exception as exc:
        source.last_error = str(exc)
        source.save(update_fields=["last_error"])
        return {"fetched": 0, "error": str(exc)}

    seen_uids = {occ["uid"] for occ in occurrences}
    for occ in occurrences:
        RemoteCalendarEntry.objects.update_or_create(
            source=source, uid=occ["uid"],
            defaults={
                "title": occ["title"],
                "description": occ.get("description", ""),
                "start": occ["start"],
                "end": occ["end"],
                "all_day": occ.get("all_day", False),
            },
        )
    RemoteCalendarEntry.objects.filter(source=source).exclude(uid__in=seen_uids).delete()

    source.last_fetched_at = now()
    source.last_error = ""
    source.save(update_fields=["last_fetched_at", "last_error"])
    return {"fetched": len(occurrences)}
