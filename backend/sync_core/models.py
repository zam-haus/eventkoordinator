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

    def __str__(self):
        return f"SyncBaseItem(entity={self.related_entity_id}, target={self.sync_target_id}, status={self.status})"


def compute_derived_state(item: SyncBaseItem) -> str:
    """The single shared derived_state computation (§3.2) — every surface
    (rego input.sync, templates, the sync_status element) reads this, never
    re-derives it independently."""
    target = item.sync_target
    if target is None or not target.enabled:
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
