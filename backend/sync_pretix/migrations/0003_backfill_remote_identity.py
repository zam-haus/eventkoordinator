"""events-and-sync.md §14: backfill remote_identity for existing legacy
(area_association-based) PretixSyncItem rows that already have a
subevent_slug, so pull_update()/delete_remote()/item_admin_url — now unified
to always read remote_identity when set — keep working for them exactly as
before. Rows without a subevent_slug yet don't need one: their first push
still goes through the (untouched) legacy path and creates the subevent
without ever pinning an identity, since only the bindings path pins one.
"""
from django.db import migrations


def backfill_remote_identity(apps, schema_editor):
    PretixSyncItem = apps.get_model("sync_pretix", "PretixSyncItem")
    qs = PretixSyncItem.objects.filter(
        subevent_slug__isnull=False, remote_identity__isnull=True,
    ).exclude(subevent_slug="").select_related("area_association", "sync_target")
    for item in qs:
        association = item.area_association
        target = item.sync_target
        if association is None or target is None:
            continue
        item.remote_identity = {
            "organizer_slug": target.organizer_slug,
            "event_slug": association.event_slug,
            "subevent_id": item.subevent_slug,
        }
        item.save(update_fields=["remote_identity"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sync_pretix", "0002_historicalpretixsyncitem_remote_identity_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_remote_identity, noop_reverse),
    ]
