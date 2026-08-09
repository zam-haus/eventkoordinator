"""Celery worker for sync_core (events-and-sync.md §4.2).

One task, beat-scheduled + manually triggerable: fetch pending items, push,
set synced/error. Snapshot semantics are deliberate — the worker pushes
exactly what mark_sync recorded in synced_payload; it never re-evaluates the
policy at push time.
"""
import logging

from celery import shared_task
from django.utils.timezone import now

logger = logging.getLogger(__name__)


def push_pending_sync_items() -> dict:
    """Push every `pending` SyncBaseItem. Returns {"pushed": n, "failed": n}.

    Plain function (not the task wrapper) so management commands and tests
    can call it synchronously without a broker — same convention as
    userdefinedmodel.tasks.run_bulk_migration.
    """
    from sync_core.models import DERIVED_STATE_ERROR, DERIVED_STATE_PENDING, DERIVED_STATE_SYNCED, SyncBaseItem

    pushed = failed = 0
    # No select_related("sync_target"): django-polymorphic only downcasts FK
    # access through its own manager (lazy access below, or get_real_instance()
    # on the item) — a select_related JOIN fetches the base SyncBaseTarget
    # row's columns only, silently handing plugin push() code a target
    # missing every subclass field (e.g. PretixSyncTarget.organizer_slug).
    items = list(
        SyncBaseItem.objects.filter(status=DERIVED_STATE_PENDING)
        .select_related("related_entity")
        .order_by("sync_target_id", "id")
    )
    for item in items:
        real = item.get_real_instance()
        try:
            real.push()
        except Exception as exc:
            real.status = DERIVED_STATE_ERROR
            real.last_error = str(exc)
            real.save(update_fields=["status", "last_error"])
            failed += 1
            logger.warning("sync push failed for item=%s target=%s: %s", real.id, real.sync_target_id, exc)
        else:
            real.status = DERIVED_STATE_SYNCED
            real.synced_at = now()
            real.last_error = ""
            real.save(update_fields=["status", "synced_at", "last_error"])
            pushed += 1
    return {"pushed": pushed, "failed": failed}


_PENDING_PUSH_QUEUED_CACHE_KEY = "sync_core:push_pending_sync_items:queued"


@shared_task
def push_pending_sync_items_task() -> dict:
    from django.core.cache import cache

    # Cleared at the start of every run (not just on success) so a
    # mark_sync() firing WHILE this task is already running still queues a
    # follow-up run for whatever became pending after this one started
    # scanning — see enqueue_push_if_idle().
    cache.delete(_PENDING_PUSH_QUEUED_CACHE_KEY)
    return push_pending_sync_items()


def enqueue_push_if_idle() -> None:
    """Queue push_pending_sync_items_task unless one is already
    queued/running. mark_sync() calls this every time it marks an item
    `pending`, so a push actually happens within seconds — without this, a
    pending item only gets pushed on the next `CELERY_BEAT_SCHEDULE` tick
    (every 10 minutes), or never, if no beat process happens to be running
    at all (mark_sync() itself never pushed synchronously — see
    events-and-sync.md §4.2's "the worker pushes exactly what mark_sync
    recorded", which assumed a worker would in fact run soon).

    The cache-based debounce coalesces a burst of mark_sync() calls (e.g.
    migrating many entities) into a single queued task rather than one per
    entity; it is a best-effort courtesy, not a correctness requirement —
    push_pending_sync_items() itself is idempotent (only ever touches rows
    still `pending`), so a duplicate/overlapping run is harmless, just
    wasted work. A broker outage must never break mark_sync() itself.
    """
    from django.core.cache import cache

    if not cache.add(_PENDING_PUSH_QUEUED_CACHE_KEY, "1", timeout=60):
        return
    try:
        push_pending_sync_items_task.delay()
    except Exception:
        cache.delete(_PENDING_PUSH_QUEUED_CACHE_KEY)
        logger.warning("failed to enqueue push_pending_sync_items_task", exc_info=True)


@shared_task
def fetch_calendar_source_task(source_id) -> dict:
    from sync_core.models import fetch_calendar_source
    return fetch_calendar_source(source_id)


@shared_task
def fetch_all_calendar_sources() -> None:
    """Dispatch fetch_calendar_source_task for every enabled CalendarSource."""
    from sync_core.models import CalendarSource

    source_ids = list(CalendarSource.objects.filter(enabled=True).values_list("pk", flat=True))
    logger.info("Dispatching fetch for %d calendar source(s)", len(source_ids))
    for source_id in source_ids:
        fetch_calendar_source_task.delay(source_id)
