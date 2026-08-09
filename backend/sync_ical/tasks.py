"""Celery entry points for sync_ical (events-and-sync.md §3/§4.2, Step 11).

The actual push work (building/updating the target's .ics feed from
synced_payload) lives on IcalCalendarSyncItem.push(); the shared
sync_core worker (sync_core.tasks.push_pending_sync_items) is what
iterates pending items polymorphically and calls it. This module just
exposes a scoped "push this target's pending items now" entry point for
the admin "Sync now" button and a beat-scheduled dispatch, consistent with
sync_core's convention of plain functions wrapped by @shared_task so tests
and management commands can call them synchronously without a broker.
"""
import logging

from celery import shared_task
from django.utils.timezone import now

logger = logging.getLogger(__name__)


def push_ical_target(sync_target_id) -> dict:
    """Push every pending IcalCalendarSyncItem for one target. Returns
    {"pushed": n, "failed": n}."""
    from sync_core.models import DERIVED_STATE_ERROR, DERIVED_STATE_PENDING, DERIVED_STATE_SYNCED
    from sync_ical.models import IcalCalendarSyncItem, IcalCalendarSyncTarget

    try:
        sync_target = IcalCalendarSyncTarget.objects.get(pk=sync_target_id)
    except IcalCalendarSyncTarget.DoesNotExist:
        logger.error("IcalCalendarSyncTarget %s not found", sync_target_id)
        return {"pushed": 0, "failed": 0}

    pushed = failed = 0
    items = IcalCalendarSyncItem.objects.filter(
        sync_target=sync_target, status=DERIVED_STATE_PENDING,
    )
    for item in items:
        try:
            item.push()
        except Exception as exc:
            item.status = DERIVED_STATE_ERROR
            item.last_error = str(exc)
            item.save(update_fields=["status", "last_error"])
            failed += 1
            logger.warning("sync_ical push failed for item=%s target=%s: %s", item.id, sync_target_id, exc)
        else:
            item.status = DERIVED_STATE_SYNCED
            item.synced_at = now()
            item.last_error = ""
            item.save(update_fields=["status", "synced_at", "last_error"])
            pushed += 1

    logger.info("iCal push complete for %r: %d pushed, %d failed", sync_target.name, pushed, failed)
    return {"pushed": pushed, "failed": failed}


@shared_task
def sync_ical_target(sync_target_id):
    """Push a single IcalCalendarSyncTarget's pending items now."""
    return push_ical_target(sync_target_id)


@shared_task
def sync_all_ical_targets():
    """Dispatch sync_ical_target for every IcalCalendarSyncTarget in the database."""
    from sync_ical.models import IcalCalendarSyncTarget

    target_ids = list(IcalCalendarSyncTarget.objects.values_list("pk", flat=True))
    logger.info("Dispatching push for %d iCal sync target(s)", len(target_ids))
    for target_id in target_ids:
        sync_ical_target.delay(target_id)
