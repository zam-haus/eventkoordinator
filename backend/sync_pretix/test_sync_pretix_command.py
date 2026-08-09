from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apiv1.models.basedata import ProposalArea
from sync_pretix.models import PretixSyncTarget, PretixSyncTargetAreaAssociation


class _FakePretixApiClient:
    def __init__(self, organizer=None, events=None, items_by_event=None):
        self.organizer = organizer
        self.events = list(events or [])
        self.items_by_event = {
            slug: list(items) for slug, items in (items_by_event or {}).items()
        }
        self.created_organizers = []
        self.patched_organizers = []
        self.created_events = []
        self.patched_events = []
        self.created_items = []

    def get_organizer(self, organizer_slug):
        if self.organizer and self.organizer.get("slug") == organizer_slug:
            return self.organizer
        return None

    def create_organizer(self, *, slug, name):
        self.created_organizers.append({"slug": slug, "name": name})
        self.organizer = {"slug": slug, "name": name}
        return self.organizer

    def patch_organizer(self, *, slug, payload):
        self.patched_organizers.append({"slug": slug, "payload": payload})
        if self.organizer and self.organizer.get("slug") == slug:
            self.organizer.update(payload)
        return self.organizer or {"slug": slug}

    def list_events(self, organizer_slug):
        return list(self.events)

    def create_event(self, *, organizer_slug, payload):
        self.created_events.append({"organizer_slug": organizer_slug, "payload": payload})
        self.events.append(
            {
                "slug": payload["slug"],
                "name": payload["name"],
                "has_subevents": payload.get("has_subevents", False),
            }
        )
        return self.events[-1]

    def patch_event(self, *, organizer_slug, event_slug, payload):
        self.patched_events.append(
            {"organizer_slug": organizer_slug, "event_slug": event_slug, "payload": payload}
        )
        for event in self.events:
            if event.get("slug") == event_slug:
                event.update(payload)
                return event
        return {"slug": event_slug, **payload}

    def list_items(self, *, organizer_slug, event_slug):
        return list(self.items_by_event.get(event_slug, []))

    def create_item(self, *, organizer_slug, event_slug, payload):
        self.created_items.append(
            {
                "organizer_slug": organizer_slug,
                "event_slug": event_slug,
                "payload": payload,
            }
        )
        self.items_by_event.setdefault(event_slug, []).append(payload)
        return payload


@override_settings(
    PRETIX_API_BASE_URL="http://pretix.local/api/v1",
    PRETIX_API_TOKEN="test-token",
    PRETIX_ORGANIZER_SLUG="zam",
    PRETIX_ORGANIZER_NAME="ZAM",
    PRETIX_EVENT_SLUG_PREFIX="area",
    PRETIX_EVENT_CURRENCY="EUR",
    PRETIX_EVENT_TIMEZONE="Europe/Berlin",
    PRETIX_EVENT_LOCALE="en",
)
class SyncPretixAreasCommandTests(TestCase):
    def setUp(self):
        ProposalArea.objects.all().delete()
        PretixSyncTargetAreaAssociation.objects.all().delete()
        PretixSyncTarget.objects.all().delete()

    def test_replaces_sync_target_and_creates_association_and_default_items(self):
        ProposalArea.objects.create(code="metal", label="Metal")
        PretixSyncTarget.objects.create(
            key="pretix:old", name="Old Pretix",
            api_token="old-token",
            api_url="http://old.local/api/v1",
            organizer_slug="old-org",
        )
        PretixSyncTarget.objects.create(
            key="pretix:old-2", name="Old Pretix 2",
            api_token="old-token-2",
            api_url="http://old2.local/api/v1",
            organizer_slug="old-org-2",
        )

        fake_client = _FakePretixApiClient(organizer={"slug": "zam", "name": "ZAM"})
        stdout = StringIO()

        with patch(
            "sync_pretix.management.commands.sync_pretix_areas.Command._build_client",
            return_value=fake_client,
        ):
            call_command("sync_pretix_areas", stdout=stdout)

        self.assertEqual(PretixSyncTarget.objects.count(), 1)
        target = PretixSyncTarget.objects.get()
        self.assertEqual(target.organizer_slug, "zam")
        self.assertEqual(target.api_url, "http://pretix.local/api/v1")
        self.assertEqual(target.api_token, "test-token")
        self.assertEqual(len(fake_client.created_events), 1)
        self.assertEqual(fake_client.created_events[0]["payload"]["slug"], "area-metal")
        self.assertTrue(fake_client.created_events[0]["payload"]["has_subevents"])
        association = PretixSyncTargetAreaAssociation.objects.get(area_code="metal")
        self.assertEqual(association.event_slug, "area-metal")
        self.assertEqual(association.sync_target_id, target.id)
        self.assertEqual(len(fake_client.created_items), 5)
        created_names = {
            item["payload"]["name"]["en"] for item in fake_client.created_items
        }
        self.assertEqual(
            created_names,
            {
                "Regular Member Ticket",
                "Discounted Member Ticket",
                "Regular Guest Ticket",
                "Discounted Guest Ticket",
                "Business Ticket",
            },
        )

    def test_updates_event_name_when_label_changed(self):
        ProposalArea.objects.create(code="laser", label="Laser Workshop")
        fake_client = _FakePretixApiClient(
            organizer={"slug": "zam", "name": "ZAM"},
            events=[{"slug": "area-laser", "name": {"en": "Old Name"}}],
            items_by_event={
                "area-laser": [
                    {"name": {"en": "Regular Member Ticket"}},
                ]
            },
        )

        with patch(
            "sync_pretix.management.commands.sync_pretix_areas.Command._build_client",
            return_value=fake_client,
        ):
            call_command("sync_pretix_areas")

        self.assertEqual(len(fake_client.created_events), 0)
        self.assertEqual(len(fake_client.patched_events), 1)
        self.assertEqual(fake_client.patched_events[0]["event_slug"], "area-laser")
        self.assertEqual(fake_client.patched_events[0]["payload"]["name"], {"en": "Laser Workshop"})
        self.assertTrue(fake_client.patched_events[0]["payload"]["has_subevents"])
        association = PretixSyncTargetAreaAssociation.objects.get(area_code="laser")
        self.assertEqual(association.event_slug, "area-laser")
        self.assertIsNotNone(association.sync_target_id)
        self.assertEqual(len(fake_client.created_items), 4)

    def test_enables_subevents_for_existing_event(self):
        ProposalArea.objects.create(code="stage", label="Main Stage")
        fake_client = _FakePretixApiClient(
            organizer={"slug": "zam", "name": "ZAM"},
            events=[
                {
                    "slug": "area-stage",
                    "name": {"en": "Main Stage"},
                    "has_subevents": False,
                }
            ],
        )

        with patch(
            "sync_pretix.management.commands.sync_pretix_areas.Command._build_client",
            return_value=fake_client,
        ):
            call_command("sync_pretix_areas")

        self.assertEqual(len(fake_client.created_events), 0)
        self.assertEqual(len(fake_client.patched_events), 1)
        self.assertEqual(
            fake_client.patched_events[0]["payload"],
            {"has_subevents": True},
        )

    def test_dry_run_does_not_mutate_remote(self):
        ProposalArea.objects.create(code="print", label="3D Print")
        PretixSyncTarget.objects.create(
            key="pretix:old", name="Old Pretix",
            api_token="old-token",
            api_url="http://old.local/api/v1",
            organizer_slug="old-org",
        )
        fake_client = _FakePretixApiClient(organizer={"slug": "zam", "name": "ZAM"})

        with patch(
            "sync_pretix.management.commands.sync_pretix_areas.Command._build_client",
            return_value=fake_client,
        ):
            call_command("sync_pretix_areas", "--dry-run")

        self.assertEqual(PretixSyncTarget.objects.count(), 1)
        self.assertEqual(fake_client.created_events, [])
        self.assertEqual(fake_client.patched_events, [])
        self.assertEqual(fake_client.created_items, [])
        self.assertEqual(PretixSyncTargetAreaAssociation.objects.count(), 0)
