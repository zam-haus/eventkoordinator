"""Celery tasks for sync_caldav (events-and-sync.md §3/§4, Step 11).

The legacy pull side (importing remote CalDAV calendars into local
`apiv1.models.Event` rows) has been superseded by the read-side pull
mechanism in `sync_core.calendar_fetch`/`sync_core.models.fetch_calendar_source`
and is out of scope here. This app now only pushes: both task names below are
kept alive (beat-scheduled in `default_settings.py`, and the admin "Sync now"
button) but simply delegate to the polymorphic sync_core worker, which pushes
every pending `SyncBaseItem` regardless of which plugin it belongs to.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def sync_caldav_target(sync_target_id=None):
    """Push pending sync items. `sync_target_id` is accepted for backwards
    compatibility with the admin "Sync now" button but is not used to filter —
    the sync_core worker (events-and-sync.md §4.2) pushes every pending item
    across all targets in one pass."""
    from sync_core.tasks import push_pending_sync_items

    result = push_pending_sync_items()
    logger.info("sync_caldav_target: %s", result)
    return result


@shared_task
def sync_all_caldav_targets():
    """Beat-scheduled entry point (default_settings.py); delegates to the
    shared sync_core worker (events-and-sync.md §4.2)."""
    from sync_core.tasks import push_pending_sync_items

    result = push_pending_sync_items()
    logger.info("sync_all_caldav_targets: %s", result)
    return result
