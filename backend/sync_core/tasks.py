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
    items = list(
        SyncBaseItem.objects.filter(status=DERIVED_STATE_PENDING)
        .select_related("sync_target", "related_entity")
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


@shared_task
def push_pending_sync_items_task() -> dict:
    return push_pending_sync_items()


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
