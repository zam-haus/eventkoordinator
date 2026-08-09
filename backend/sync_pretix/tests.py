from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apiv1.models.basedata import Event, Proposal, Series
from apiv1.models.sync.syncbasedata import SyncBaseTarget
from sync_pretix.models import (
    CalculatedPrices,
    PretixPricingConfiguration,
    PretixSyncItem,
    PretixSyncTarget,
    PretixSyncTargetAreaAssociation,
)
from apiv1.models.basedata import ProposalArea


# ---------------------------------------------------------------------------
# Helpers shared by multiple test classes
# ---------------------------------------------------------------------------

def _make_pretix_client_mock(
    *,
    subevent: dict | None = None,
    quotas: list | None = None,
    pretix_event: dict | None = None,
    items: list | None = None,
    created_subevent_id: str = "7",
):
    """Return a MagicMock that mimics PretixApiClient."""
    client = MagicMock()
    client.get_subevent.return_value = subevent or {
        "id": 7,
        "name": {"de": "Workshop"},
        "date_from": "2026-05-01T10:00:00+02:00",
        "date_to": "2026-05-01T12:00:00+02:00",
        "active": True,
        "item_price_overrides": [],
    }
    client.list_quotas.return_value = quotas if quotas is not None else [
        {"id": 1, "size": 10, "items": []}
    ]
    client.get_event.return_value = pretix_event or {"locales": ["de"]}
    client.list_items.return_value = items or []
    client.create_subevent.return_value = {"id": int(created_subevent_id)}
    client.patch_subevent.return_value = {}
    client.list_quotas.return_value = quotas if quotas is not None else [
        {"id": 1, "size": 10, "items": []}
    ]
    client.patch_quota.return_value = {}
    client.create_quota.return_value = {}
    return client


class _PretixSyncItemTestBase(TestCase):
    """Shared setUp for PretixSyncItem tests."""

    def setUp(self):
        self.area = ProposalArea.objects.create(code="metal", label="Metal")
        self.series = Series.objects.create(name="Test Series")
        now = timezone.now().replace(microsecond=0)
        self.start_time = now
        self.end_time = now + timezone.timedelta(hours=2)
        self.proposal = Proposal.objects.create(
            title="Metal Workshop",
            abstract="a" * 50,
            description="d" * 50,
            material_cost_eur=Decimal("3.00"),
            preferred_dates="Any",
            max_participants=10,
        )
        self.event = Event.objects.create(
            series=self.series,
            proposal=self.proposal,
            name="Metal Workshop",
            start_time=self.start_time,
            end_time=self.end_time,
        )
        self.target = PretixSyncTarget.objects.create(
            api_token="test-token",
            api_url="https://pretix.example.com/api/v1",
            organizer_slug="zam",
        )
        self.association = PretixSyncTargetAreaAssociation.objects.create(
            sync_target=self.target,
            area=self.area,
            event_slug="area-metal",
        )
        self.item = PretixSyncItem.objects.create(
            sync_target=self.target,
            related_event=self.event,
            area_association=self.association,
        )


# ---------------------------------------------------------------------------
# PretixSyncItem.pull_update()
# ---------------------------------------------------------------------------

class PretixSyncItemPullUpdateTest(_PretixSyncItemTestBase):
    """Tests for PretixSyncItem.pull_update()."""

    def test_pull_update_no_op_when_no_subevent_slug(self):
        """pull_update() must be silent when subevent_slug is not set."""
        self.assertIsNone(self.item.subevent_slug)
        with patch("sync_pretix.models.PretixApiClient") as mock_cls:
            self.item.pull_update()
        mock_cls.assert_not_called()
        self.item.refresh_from_db()
        self.assertIsNone(self.item.pretix_data)

    def test_pull_update_fetches_and_stores_subevent_and_quotas(self):
        """pull_update() fetches subevent + quotas + items and persists them as pretix_data."""
        self.item.subevent_slug = "7"
        self.item.save(update_fields=["subevent_slug"])

        fake_subevent = {
            "id": 7,
            "name": {"de": "Metal Workshop"},
            "date_from": self.start_time.isoformat(),
            "date_to": self.end_time.isoformat(),
        }
        fake_quotas = [{"id": 1, "size": 10, "items": []}]
        fake_items = [{"id": 101, "name": {"de": "Regular Member Ticket"}}]
        fake_client = _make_pretix_client_mock(
            subevent=fake_subevent,
            quotas=fake_quotas,
            items=fake_items,
        )

        with patch("sync_pretix.models.PretixApiClient", return_value=fake_client):
            self.item.pull_update()

        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.pretix_data)
        self.assertEqual(self.item.pretix_data["subevent"], fake_subevent)
        self.assertEqual(self.item.pretix_data["quotas"], fake_quotas)
        self.assertEqual(self.item.pretix_data["items"], fake_items)

        fake_client.get_subevent.assert_called_once_with(
            organizer_slug="zam",
            event_slug="area-metal",
            subevent_id="7",
        )
        fake_client.list_quotas.assert_called_once_with(
            organizer_slug="zam",
            event_slug="area-metal",
            subevent_id="7",
        )
        fake_client.list_items.assert_called_once_with(
            organizer_slug="zam",
            event_slug="area-metal",
        )

    def test_pull_update_raises_when_no_association(self):
        """pull_update() raises ValueError when area_association is not set."""
        self.item.subevent_slug = "99"
        self.item.area_association = None
        self.item.save(update_fields=["subevent_slug", "area_association"])

        with self.assertRaises(ValueError):
            self.item.pull_update()


# ---------------------------------------------------------------------------
# PretixSyncItem.sync_diff()
# ---------------------------------------------------------------------------

class PretixSyncItemSyncDiffTest(_PretixSyncItemTestBase):
    """Tests for PretixSyncItem.sync_diff()."""

    def test_sync_diff_returns_creation_preview_when_no_subevent_slug(self):
        """sync_diff() returns a creation preview diff when subevent_slug is None."""
        self.assertIsNone(self.item.subevent_slug)
        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        self.assertIn("name", prop_names)
        self.assertIn("date_from", prop_names)
        self.assertIn("date_to", prop_names)
        # All remote values are empty (nothing in Pretix yet)
        for p in diff.properties:
            self.assertEqual(p.remote_value, "", f"Expected empty remote_value for {p.property_name}")
        # Local values are populated from the event
        name_diff = next(p for p in diff.properties if p.property_name == "name")
        self.assertEqual(name_diff.local_value, self.event.name)

    def test_sync_diff_returns_none_when_no_pretix_data(self):
        """sync_diff() must return None when pretix_data has not been pulled yet."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = None
        self.item.save(update_fields=["subevent_slug", "pretix_data"])
        self.assertIsNone(self.item.sync_diff())

    def test_sync_diff_returns_empty_properties_when_in_sync(self):
        """sync_diff() returns SyncDiffData with no properties when everything matches."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
                "frontpage_text": {
                    "de": f"{self.proposal.abstract}\n---\n{self.proposal.description}"
                },
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        self.assertEqual(diff.properties, [])

    def test_sync_diff_detects_date_from_difference(self):
        """sync_diff() reports a property diff when date_from is wrong in Pretix."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": "2020-01-01T00:00:00+00:00",  # wrong date
                "date_to": self.end_time.isoformat(),
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        self.assertIn("date_from", prop_names)

    def test_sync_diff_detects_date_to_difference(self):
        """sync_diff() reports a property diff when date_to is wrong in Pretix."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": self.start_time.isoformat(),
                "date_to": "2020-01-01T00:00:00+00:00",  # wrong date
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        self.assertIn("date_to", prop_names)

    def test_sync_diff_detects_name_difference(self):
        """sync_diff() reports a property diff when the subevent name in Pretix differs."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": "Wrong Name"},  # wrong name
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        self.assertIn("name", prop_names)
        name_diff = next(p for p in diff.properties if p.property_name == "name")
        self.assertEqual(name_diff.local_value, self.event.name)
        self.assertEqual(name_diff.remote_value, "Wrong Name")

    def test_sync_diff_detects_quota_size_difference(self):
        """sync_diff() reports a property diff when the quota size in Pretix differs."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
            },
            "quotas": [{"id": 1, "size": 999, "items": []}],  # wrong size
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        self.assertIn("quota_size", prop_names)
        quota_diff = next(p for p in diff.properties if p.property_name == "quota_size")
        self.assertEqual(quota_diff.local_value, str(self.proposal.max_participants))
        self.assertEqual(quota_diff.remote_value, "999")

    def test_sync_diff_handles_timezone_equivalent_dates_as_equal(self):
        """sync_diff() must not flag dates that represent the same instant in different TZs."""
        from datetime import datetime, timezone as _tz, timedelta
        utc_start = self.start_time.astimezone(_tz.utc)
        berlin_offset = timedelta(hours=2)
        berlin_start = utc_start.astimezone(_tz(berlin_offset))

        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": berlin_start.isoformat(),  # same instant, different TZ repr
                "date_to": self.end_time.isoformat(),
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        self.assertNotIn("date_from", prop_names)


# ---------------------------------------------------------------------------
# PretixSyncItem.sync_diff(only_differences=False)
# ---------------------------------------------------------------------------

class PretixSyncItemSyncDiffAllPropertiesTest(_PretixSyncItemTestBase):
    """Tests for PretixSyncItem.sync_diff(only_differences=False).

    When only_differences=False all comparable properties are returned so the
    diff viewer can show the full context even when the item is up-to-date.
    """

    def _in_sync_pretix_data(self):
        return {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
                "item_price_overrides": [],
                "frontpage_text": {
                    "de": f"{self.proposal.abstract}\n---\n{self.proposal.description}"
                },
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
            "items": [],
        }

    def test_sync_diff_all_properties_includes_all_when_in_sync(self):
        """With only_differences=False, date_from/date_to/name/quota_size are all returned
        even when local and remote values match."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._in_sync_pretix_data()
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff(only_differences=False)
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        self.assertIn("date_from", prop_names)
        self.assertIn("date_to", prop_names)
        self.assertIn("name", prop_names)
        self.assertIn("quota_size", prop_names)

    def test_sync_diff_all_properties_values_match_when_in_sync(self):
        """With only_differences=False, local_value and remote_value are equal for matching
        properties."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._in_sync_pretix_data()
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff(only_differences=False)
        self.assertIsNotNone(diff)
        name_prop = next(p for p in diff.properties if p.property_name == "name")
        self.assertEqual(name_prop.local_value, self.event.name)
        self.assertEqual(name_prop.remote_value, self.event.name)

    def test_sync_diff_all_properties_still_includes_differences(self):
        """With only_differences=False, properties that DO differ are still returned."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": "WRONG NAME"},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
                "item_price_overrides": [],
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
            "items": [],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff(only_differences=False)
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        # All properties present
        self.assertIn("date_from", prop_names)
        self.assertIn("date_to", prop_names)
        self.assertIn("name", prop_names)
        # name differs
        name_prop = next(p for p in diff.properties if p.property_name == "name")
        self.assertNotEqual(name_prop.local_value, name_prop.remote_value)

    def test_sync_diff_default_still_returns_empty_when_in_sync(self):
        """The default only_differences=True still returns empty properties when in sync,
        to avoid breaking get_status()."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._in_sync_pretix_data()
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()  # default: only_differences=True
        self.assertIsNotNone(diff)
        self.assertEqual(diff.properties, [])

    def test_sync_diff_all_returns_none_when_no_pretix_data(self):
        """With only_differences=False, None is still returned when pretix_data is absent."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = None
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        self.assertIsNone(self.item.sync_diff(only_differences=False))

    def test_sync_diff_all_returns_creation_preview_when_no_subevent(self):
        """With only_differences=False, creation preview is returned when subevent_slug
        is absent (behaves same as only_differences=True)."""
        self.assertIsNone(self.item.subevent_slug)
        diff = self.item.sync_diff(only_differences=False)
        self.assertIsNotNone(diff)
        # Creation preview always has all properties with remote_value=""
        for p in diff.properties:
            self.assertEqual(p.remote_value, "")


# ---------------------------------------------------------------------------
# SyncBaseItem.get_status()
# ---------------------------------------------------------------------------

class PretixSyncItemGetStatusTest(_PretixSyncItemTestBase):
    """Tests for PretixSyncItem.get_status() (item-level status)."""

    def test_get_status_returns_creation_pending_when_no_subevent_slug(self):
        """Status is CREATION_PENDING when the item has been created locally but not pushed."""
        self.assertIsNone(self.item.subevent_slug)
        self.assertEqual(
            self.item.get_status(),
            SyncBaseTarget.SyncTargetStatus.CREATION_PENDING,
        )

    def test_get_status_returns_status_unknown_when_pretix_data_is_none(self):
        """Status is STATUS_UNKNOWN when subevent_slug is set but pretix_data not pulled yet."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = None
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        self.assertEqual(
            self.item.get_status(),
            SyncBaseTarget.SyncTargetStatus.STATUS_UNKNOWN,
        )

    def test_get_status_returns_up_to_date_when_in_sync(self):
        """Status is ENTRY_UP_TO_DATE when all compared fields match."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
                "frontpage_text": {
                    "de": f"{self.proposal.abstract}\n---\n{self.proposal.description}"
                },
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        self.assertEqual(
            self.item.get_status(),
            SyncBaseTarget.SyncTargetStatus.ENTRY_UP_TO_DATE,
        )

    def test_get_status_returns_differs_when_name_mismatch(self):
        """Status is ENTRY_DIFFERS when the subevent name in Pretix is wrong."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": "Wrong Name"},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        self.assertEqual(
            self.item.get_status(),
            SyncBaseTarget.SyncTargetStatus.ENTRY_DIFFERS,
        )


# ---------------------------------------------------------------------------
# SyncBaseTarget.get_status() now aggregates item.get_status()
# ---------------------------------------------------------------------------

class PretixSyncTargetGetStatusTest(_PretixSyncItemTestBase):
    """Tests for SyncBaseTarget.get_status() delegating to item.get_status()."""

    def test_target_status_no_entry_when_no_items(self):
        """NO_ENTRY_EXISTS only when there are no sync items at all."""
        PretixSyncItem.objects.filter(related_event=self.event).delete()
        self.assertEqual(
            self.target.get_status(self.event),
            SyncBaseTarget.SyncTargetStatus.NO_ENTRY_EXISTS,
        )

    def test_target_status_creation_pending_when_item_not_pushed(self):
        """When all items have no subevent_slug, aggregate is CREATION_PENDING."""
        self.assertIsNone(self.item.subevent_slug)
        self.assertEqual(
            self.target.get_status(self.event),
            SyncBaseTarget.SyncTargetStatus.CREATION_PENDING,
        )

    def test_target_status_unknown_when_slug_set_but_no_data(self):
        """STATUS_UNKNOWN bubbles up to the target when an item has slug but no pretix_data."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = None
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        self.assertEqual(
            self.target.get_status(self.event),
            SyncBaseTarget.SyncTargetStatus.STATUS_UNKNOWN,
        )

    def test_target_status_up_to_date_when_in_sync(self):
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
                "frontpage_text": {
                    "de": f"{self.proposal.abstract}\n---\n{self.proposal.description}"
                },
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        self.assertEqual(
            self.target.get_status(self.event),
            SyncBaseTarget.SyncTargetStatus.ENTRY_UP_TO_DATE,
        )

    def test_target_status_differs_when_any_item_differs(self):
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "name": {"de": "Wrong Name"},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
            },
            "quotas": [],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        self.assertEqual(
            self.target.get_status(self.event),
            SyncBaseTarget.SyncTargetStatus.ENTRY_DIFFERS,
        )


# ---------------------------------------------------------------------------
# push_update() triggers pull_update()
# ---------------------------------------------------------------------------

class PretixSyncItemPushCallsPullTest(_PretixSyncItemTestBase):
    """push_update() must call pull_update() at the end to refresh pretix_data."""

    def test_push_update_calls_pull_after_push(self):
        """After a successful push, pretix_data is populated via pull_update()."""
        pulled_subevent = {
            "id": 7,
            "name": {"de": self.event.name},
            "date_from": self.start_time.isoformat(),
            "date_to": self.end_time.isoformat(),
        }
        pulled_quotas = [{"id": 1, "size": int(self.proposal.max_participants), "items": []}]
        fake_client = _make_pretix_client_mock(
            subevent=pulled_subevent,
            quotas=pulled_quotas,
            created_subevent_id="7",
        )

        with patch("sync_pretix.models.PretixApiClient", return_value=fake_client):
            self.item.push_update()

        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.pretix_data)
        self.assertEqual(self.item.pretix_data["subevent"], pulled_subevent)
        self.assertEqual(self.item.pretix_data["quotas"], pulled_quotas)
        # Verify pull was called with the correct subevent id
        fake_client.get_subevent.assert_called_with(
            organizer_slug="zam",
            event_slug="area-metal",
            subevent_id=self.item.subevent_slug,
        )

    def test_push_update_calls_pull_even_when_push_fails(self):
        """pull_update() must run in the finally block even when push_update() raises."""
        pulled_subevent = {
            "id": 7,
            "name": {"de": self.event.name},
            "date_from": self.start_time.isoformat(),
            "date_to": self.end_time.isoformat(),
        }
        pulled_quotas = [{"id": 1, "size": int(self.proposal.max_participants), "items": []}]

        fake_client = _make_pretix_client_mock(
            subevent=pulled_subevent,
            quotas=pulled_quotas,
            created_subevent_id="7",
        )
        # Make patch_subevent fail after the subevent has been created.
        fake_client.patch_subevent.side_effect = Exception("Pretix API error")

        with patch("sync_pretix.models.PretixApiClient", return_value=fake_client):
            with self.assertRaises(Exception, msg="Pretix API error"):
                self.item.push_update()

        self.item.refresh_from_db()
        # subevent was created (slug stored) but patch failed — pull still ran
        self.assertIsNotNone(self.item.subevent_slug)
        self.assertIsNotNone(self.item.pretix_data)
        fake_client.get_subevent.assert_called()

    def test_status_is_unknown_between_push_and_pull_when_pull_fails(self):
        """If pull_update() fails, pretix_data stays None → STATUS_UNKNOWN."""
        fake_client = _make_pretix_client_mock(created_subevent_id="7")
        # Simulate pull failure
        fake_client.get_subevent.side_effect = Exception("Network error")

        with patch("sync_pretix.models.PretixApiClient", return_value=fake_client):
            # push should still succeed (pull failure is swallowed in finally)
            self.item.push_update()

        self.item.refresh_from_db()
        # subevent_slug was set by the successful push
        self.assertIsNotNone(self.item.subevent_slug)
        # pretix_data is still None because pull failed
        self.assertIsNone(self.item.pretix_data)
        # status should be STATUS_UNKNOWN
        self.assertEqual(
            self.item.get_status(),
            SyncBaseTarget.SyncTargetStatus.STATUS_UNKNOWN,
        )


class PretixPricingConfigurationTests(TestCase):

	def test_min_participants_thresholds_are_sorted_and_normalized(self):
		self.config.min_participants_params = {"7": "2", "0": "1"}
		self.config.save(update_fields=["min_participants_params"])

		self.assertEqual(self.config.min_participants_thresholds, [(0, 1), (7, 2)])

	def test_calculated_prices_match_documentation_example(self):
		prices = self.config.get_calculated_prices(
			duration_hours=1.5,
			material_cost=3.0,
			max_participants=8,
			is_basic_course=True,
		)

		self.assertEqual(prices.member_regular_gross_eur, Decimal("17.00"))
		self.assertEqual(prices.member_discounted_gross_eur, Decimal("16.00"))
		self.assertEqual(prices.guest_regular_gross_eur, Decimal("20.00"))
		self.assertEqual(prices.guest_discounted_gross_eur, Decimal("17.00"))
		self.assertEqual(prices.business_net_eur, Decimal("32.00"))
		self.assertIsInstance(prices.member_regular_gross_eur, Decimal)
		self.assertIsInstance(prices.member_discounted_gross_eur, Decimal)
		self.assertIsInstance(prices.guest_regular_gross_eur, Decimal)
		self.assertIsInstance(prices.guest_discounted_gross_eur, Decimal)

	def test_guest_discounted_matches_sheet_logic(self):
		self.assertEqual(
			self.config.get_guest_discounted_price(
				duration_hours=2,
				material_cost=5,
				max_participants=10,
				is_basic_course=False,
			),
			self.config.get_member_regular_price(
				duration_hours=2,
				material_cost=5,
				max_participants=10,
				is_basic_course=False,
			),
		)


class PretixSyncItemPriceDiffTest(_PretixSyncItemTestBase):
    """Tests for price comparison in PretixSyncItem.sync_diff() and creation preview."""

    ITEMS = [
        {"id": 101, "name": {"de": "Regular Member Ticket"}},
        {"id": 102, "name": {"de": "Discounted Member Ticket"}},
        {"id": 103, "name": {"de": "Regular Guest Ticket"}},
        {"id": 104, "name": {"de": "Discounted Guest Ticket"}},
        {"id": 105, "name": {"de": "Business Ticket"}},
    ]

    def setUp(self):
        super().setUp()
        # Assign numeric product IDs to the association so _resolve_item_id matches by int ID.
        self.association.ticket_product_member_regular_id = "101"
        self.association.ticket_product_member_discounted_id = "102"
        self.association.ticket_product_guest_regular_id = "103"
        self.association.ticket_product_guest_discounted_id = "104"
        self.association.ticket_product_business_id = "105"
        self.association.save()

        self.prices = CalculatedPrices.objects.create(
            event=self.event,
            member_regular_gross_eur=Decimal("20.00"),
            member_discounted_gross_eur=Decimal("12.00"),
            guest_regular_gross_eur=Decimal("25.00"),
            guest_discounted_gross_eur=Decimal("20.00"),
            business_net_eur=Decimal("40.00"),
        )

    def _matching_pretix_data(self, overrides=None):
        """Build pretix_data that matches the event and association, with custom overrides."""
        if overrides is None:
            overrides = [
                {"item": 101, "price": "20.00"},
                {"item": 102, "price": "12.00"},
                {"item": 103, "price": "25.00"},
                {"item": 104, "price": "20.00"},
                {"item": 105, "price": "40.00"},
            ]
        return {
            "subevent": {
                "name": {"de": self.event.name},
                "date_from": self.start_time.isoformat(),
                "date_to": self.end_time.isoformat(),
                "item_price_overrides": overrides,
            },
            "quotas": [{"id": 1, "size": int(self.proposal.max_participants), "items": []}],
            "items": self.ITEMS,
        }

    def test_sync_diff_no_price_diff_when_prices_match(self):
        """sync_diff() reports no price diffs when stored prices match calculated prices."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._matching_pretix_data()
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        for name in ("price_member_regular", "price_member_discounted",
                     "price_guest_regular", "price_guest_discounted", "price_business"):
            self.assertNotIn(name, prop_names)

    def test_sync_diff_detects_price_difference(self):
        """sync_diff() reports a price diff when a stored price differs from calculated."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._matching_pretix_data(overrides=[
            {"item": 101, "price": "99.00"},  # wrong
            {"item": 102, "price": "12.00"},
            {"item": 103, "price": "25.00"},
            {"item": 104, "price": "20.00"},
            {"item": 105, "price": "40.00"},
        ])
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        self.assertIn("price_member_regular", prop_names)
        price_diff = next(p for p in diff.properties if p.property_name == "price_member_regular")
        self.assertEqual(price_diff.local_value, "20.00")
        self.assertEqual(price_diff.remote_value, "99.00")

    def test_sync_diff_detects_all_five_price_differences(self):
        """sync_diff() reports diffs for all five ticket types when all prices are wrong."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._matching_pretix_data(overrides=[
            {"item": 101, "price": "1.00"},
            {"item": 102, "price": "1.00"},
            {"item": 103, "price": "1.00"},
            {"item": 104, "price": "1.00"},
            {"item": 105, "price": "1.00"},
        ])
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        prop_names = [p.property_name for p in diff.properties]
        for name in ("price_member_regular", "price_member_discounted",
                     "price_guest_regular", "price_guest_discounted", "price_business"):
            self.assertIn(name, prop_names)

    def test_sync_diff_detects_missing_price_override(self):
        """sync_diff() reports a diff when no item_price_override exists for a product."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._matching_pretix_data(overrides=[])
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        prop_names = [p.property_name for p in diff.properties]
        self.assertIn("price_member_regular", prop_names)
        price_diff = next(p for p in diff.properties if p.property_name == "price_member_regular")
        self.assertEqual(price_diff.local_value, "20.00")
        self.assertEqual(price_diff.remote_value, "")

    def test_sync_diff_skips_price_comparison_without_calculated_prices(self):
        """sync_diff() skips price comparison when no CalculatedPrices exist for the event."""
        self.prices.delete()
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._matching_pretix_data()
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        prop_names = [p.property_name for p in diff.properties]
        self.assertNotIn("price_member_regular", prop_names)

    def test_sync_diff_skips_price_comparison_when_items_not_stored(self):
        """sync_diff() skips price comparison when pretix_data contains no items list."""
        self.item.subevent_slug = "7"
        data = self._matching_pretix_data()
        data["items"] = []
        self.item.pretix_data = data
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        prop_names = [p.property_name for p in diff.properties]
        self.assertNotIn("price_member_regular", prop_names)

    def test_sync_diff_price_comparison_tolerates_equivalent_decimal_strings(self):
        """sync_diff() treats '20.0' and '20.00' as equal prices."""
        self.item.subevent_slug = "7"
        self.item.pretix_data = self._matching_pretix_data(overrides=[
            {"item": 101, "price": "20.0"},   # equivalent to "20.00"
            {"item": 102, "price": "12.00"},
            {"item": 103, "price": "25.00"},
            {"item": 104, "price": "20.00"},
            {"item": 105, "price": "40.00"},
        ])
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

        diff = self.item.sync_diff()
        prop_names = [p.property_name for p in diff.properties]
        self.assertNotIn("price_member_regular", prop_names)

    def test_creation_preview_includes_prices_when_calculated_prices_exist(self):
        """_build_creation_preview_diff() includes price properties when CalculatedPrices exist."""
        self.assertIsNone(self.item.subevent_slug)
        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        for name in ("price_member_regular", "price_member_discounted",
                     "price_guest_regular", "price_guest_discounted", "price_business"):
            self.assertIn(name, prop_names)
        # All price remote_values must be empty in a creation preview.
        for p in diff.properties:
            if p.property_name.startswith("price_"):
                self.assertEqual(p.remote_value, "",
                                 f"Expected empty remote_value for {p.property_name}")
                self.assertNotEqual(p.local_value, "",
                                    f"Expected non-empty local_value for {p.property_name}")

    def test_creation_preview_omits_prices_without_calculated_prices(self):
        """_build_creation_preview_diff() omits price properties when no CalculatedPrices exist."""
        self.prices.delete()
        # Refresh to clear Django's cached reverse-relation on the related_event instance.
        self.item.refresh_from_db()
        self.assertIsNone(self.item.subevent_slug)
        diff = self.item.sync_diff()
        self.assertIsNotNone(diff)
        prop_names = [p.property_name for p in diff.properties]
        for name in ("price_member_regular", "price_member_discounted",
                     "price_guest_regular", "price_guest_discounted", "price_business"):
            self.assertNotIn(name, prop_names)


class PretixPricingConfigurationTests(TestCase):
	def setUp(self):
		self.config = PretixPricingConfiguration.objects.create()
		self.series = Series.objects.create(name="Series")
		self.proposal = Proposal.objects.create(
			title="Workshop",
			abstract="a" * 50,
			description="d" * 50,
			material_cost_eur=Decimal("3.00"),
			preferred_dates="Any",
			duration_days=2,
			duration_time_per_day="01:30",
			max_participants=8,
			is_basic_course=True,
		)
		now = timezone.now()
		self.event = Event.objects.create(
			series=self.series,
			proposal=self.proposal,
			name="Event",
			start_time=now,
			end_time=now,
		)

	def test_save_populates_empty_price_fields_from_linked_event_proposal(self):
		prices = CalculatedPrices.objects.create(event=self.event)
		self.assertEqual(prices.duration_hours, Decimal("3"))
		self.assertEqual(prices.pricing_configuration, self.config)

		expected = self.config.get_calculated_prices(
			duration_hours=Decimal("3"),
			material_cost=Decimal("3.00"),
			max_participants=8,
			is_basic_course=True,
		)
		self.assertEqual(prices.member_regular_gross_eur, expected.member_regular_gross_eur)
		self.assertEqual(prices.member_discounted_gross_eur, expected.member_discounted_gross_eur)
		self.assertEqual(prices.guest_regular_gross_eur, expected.guest_regular_gross_eur)
		self.assertEqual(prices.guest_discounted_gross_eur, expected.guest_discounted_gross_eur)
		self.assertEqual(prices.business_net_eur, expected.business_net_eur)

	def test_save_uses_explicit_pricing_configuration(self):
		custom = PretixPricingConfiguration.objects.create(lecturer_rate=200)
		prices = CalculatedPrices.objects.create(
			event=self.event,
			pricing_configuration=custom,
		)
		self.assertEqual(prices.pricing_configuration, custom)

		expected = custom.get_calculated_prices(
			duration_hours=Decimal("3"),
			material_cost=Decimal("3.00"),
			max_participants=8,
			is_basic_course=True,
		)
		self.assertEqual(prices.member_regular_gross_eur, expected.member_regular_gross_eur)

	def test_save_uses_newest_pricing_configuration_when_not_specified(self):
		newer = PretixPricingConfiguration.objects.create(lecturer_rate=120)
		prices = CalculatedPrices.objects.create(event=self.event)

		self.assertEqual(prices.pricing_configuration, newer)

	def test_save_creates_pricing_configuration_when_none_exist(self):
		PretixPricingConfiguration.objects.all().delete()
		prices = CalculatedPrices.objects.create(event=self.event)

		self.assertIsNotNone(prices.pricing_configuration)
		self.assertEqual(PretixPricingConfiguration.objects.count(), 1)

	def test_save_keeps_manually_provided_fields(self):
		prices = CalculatedPrices.objects.create(
			event=self.event,
			member_regular_gross_eur=Decimal("999.00"),
		)
		self.assertEqual(prices.member_regular_gross_eur, Decimal("999.00"))
		self.assertIsInstance(prices.member_regular_gross_eur, Decimal)
		self.assertIsNotNone(prices.member_discounted_gross_eur)
		self.assertIsNotNone(prices.guest_regular_gross_eur)
		self.assertIsNotNone(prices.guest_discounted_gross_eur)
		self.assertIsNotNone(prices.business_net_eur)

	def test_event_without_proposal_raises_validation_error(self):
		now = timezone.now()
		event = Event.objects.create(
			series=self.series,
			proposal=None,
			name="No Proposal",
			start_time=now,
			end_time=now,
		)
		prices = CalculatedPrices(event=event)

		with self.assertRaises(ValidationError):
			prices.save()
