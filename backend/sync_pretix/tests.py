from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apiv1.models.basedata import Event, Proposal, Series
from sync_core.models import SyncBaseItem
from sync_pretix.models import (
    CalculatedPrices,
    PretixPricingConfiguration,
    PretixSyncItem,
    PretixSyncTarget,
    PretixSyncTargetAreaAssociation,
)
from userdefinedmodel.tests.factories import make_entity_with_type


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
    client.patch_quota.return_value = {}
    client.create_quota.return_value = {}
    return client


DEFAULT_PAYLOAD = {
    "title": "Workshop",
    "start": "2026-05-01T10:00:00+02:00",
    "end": "2026-05-01T12:00:00+02:00",
    "locale": "de",
    "max_participants": 10,
}


class _PretixSyncItemTestBase(TestCase):
    """Shared setUp for PretixSyncItem tests: a UDM entity stands in for the
    "event" (events-and-sync.md §3, Step 11 — items relate to a generic
    UserDefinedModelEntity, not apiv1.models.Event), and `synced_payload`
    stands in for the effective-values snapshot mark_sync would take."""

    databases = ["default"]

    def setUp(self):
        self.entity, *_ = make_entity_with_type()
        self.target = PretixSyncTarget.objects.create(
            key="pretix:main", name="Pretix",
            api_token="test-token",
            api_url="https://pretix.example.com/api/v1",
            organizer_slug="zam",
        )
        self.association = PretixSyncTargetAreaAssociation.objects.create(
            sync_target=self.target,
            area_code="metal",
            event_slug="area-metal",
        )
        self.item = PretixSyncItem.objects.create(
            sync_target=self.target,
            related_entity=self.entity,
            area_association=self.association,
            synced_payload=dict(DEFAULT_PAYLOAD),
        )


# ---------------------------------------------------------------------------
# PretixSyncItem.pull_update()
# ---------------------------------------------------------------------------

class PretixSyncItemPullUpdateTest(_PretixSyncItemTestBase):

    def test_pull_update_no_op_when_no_subevent_slug(self):
        self.assertIsNone(self.item.subevent_slug)
        with patch("sync_pretix.models.PretixApiClient") as mock_cls:
            self.item.pull_update()
        mock_cls.assert_not_called()
        self.item.refresh_from_db()
        self.assertIsNone(self.item.pretix_data)

    def test_pull_update_fetches_and_stores_subevent_and_quotas(self):
        self.item.subevent_slug = "7"
        self.item.save(update_fields=["subevent_slug"])

        fake_subevent = {
            "id": 7, "name": {"de": "Workshop"},
            "date_from": "2026-05-01T10:00:00+02:00",
            "date_to": "2026-05-01T12:00:00+02:00",
        }
        fake_quotas = [{"id": 1, "size": 10, "items": [1, 2]}]
        fake_items = [{"id": 1, "name": {"de": "Regular"}}]

        with patch("sync_pretix.models.PretixApiClient") as mock_cls:
            client = mock_cls.return_value
            client.get_subevent.return_value = fake_subevent
            client.list_quotas.return_value = fake_quotas
            client.list_items.return_value = fake_items
            self.item.pull_update()

        self.item.refresh_from_db()
        self.assertEqual(self.item.pretix_data["subevent"], fake_subevent)
        self.assertEqual(self.item.pretix_data["quotas"], fake_quotas)
        self.assertEqual(self.item.pretix_data["items"], fake_items)

    def test_pull_update_raises_when_no_association(self):
        self.item.subevent_slug = "7"
        self.item.area_association = None
        self.item.save(update_fields=["subevent_slug", "area_association"])
        with self.assertRaises(ValueError):
            self.item.pull_update()


# ---------------------------------------------------------------------------
# PretixSyncItem.push()
# ---------------------------------------------------------------------------

class PretixSyncItemPushTest(_PretixSyncItemTestBase):

    def test_push_requires_area_association(self):
        self.item.area_association = None
        self.item.save(update_fields=["area_association"])
        with self.assertRaises(RuntimeError):
            self.item.push()

    def test_push_creates_subevent_on_first_push(self):
        client = _make_pretix_client_mock()
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        self.item.refresh_from_db()
        self.assertEqual(self.item.subevent_slug, "7")
        client.create_subevent.assert_called_once()
        create_payload = client.create_subevent.call_args.kwargs["payload"]
        self.assertEqual(create_payload["date_from"], DEFAULT_PAYLOAD["start"])
        self.assertEqual(create_payload["date_to"], DEFAULT_PAYLOAD["end"])
        self.assertEqual(create_payload["name"]["de"], "Workshop")

    def test_push_patches_existing_subevent_without_recreating(self):
        self.item.subevent_slug = "7"
        self.item.save(update_fields=["subevent_slug"])
        client = _make_pretix_client_mock()
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        client.create_subevent.assert_not_called()
        client.patch_subevent.assert_called_once()

    def test_push_applies_price_overrides(self):
        self.item.synced_payload = {
            **DEFAULT_PAYLOAD,
            "prices": {"member_regular": "17.00", "business": "32.00"},
        }
        self.item.save(update_fields=["synced_payload"])
        items = [
            {"id": 1, "name": {"de": "Regular Member Ticket"}},
            {"id": 2, "name": {"de": "Business Ticket"}},
        ]
        client = _make_pretix_client_mock(items=items)
        association = self.association
        association.ticket_product_member_regular_id = "Regular Member Ticket"
        association.ticket_product_business_id = "Business Ticket"
        association.save()

        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        overrides = client.create_subevent.call_args.kwargs["payload"]["item_price_overrides"]
        self.assertCountEqual(overrides, [
            {"item": 1, "price": "17.00"},
            {"item": 2, "price": "32.00"},
        ])

    def test_push_creates_quota_with_max_participants(self):
        client = _make_pretix_client_mock()
        client.list_quotas.side_effect = [[], [{"id": 1, "size": 10, "items": []}]]
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        client.create_quota.assert_called_once()
        quota_payload = client.create_quota.call_args.kwargs["payload"]
        self.assertEqual(quota_payload["size"], 10)

    def test_push_wraps_api_error_and_still_pulls(self):
        client = _make_pretix_client_mock()
        client.get_event.side_effect = RuntimeError("boom")
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            with self.assertRaises(RuntimeError):
                self.item.push()
        # pull_update() is attempted in the finally block even on failure;
        # with no subevent_slug set yet it's a no-op, so get_subevent is not called.
        client.get_subevent.assert_not_called()

    def test_push_pulls_after_successful_push(self):
        client = _make_pretix_client_mock()
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()
        client.get_subevent.assert_called_once()

    def test_push_pull_failure_does_not_mask_original_exception(self):
        client = _make_pretix_client_mock()
        client.get_event.side_effect = RuntimeError("push failed")
        self.item.subevent_slug = "7"
        self.item.save(update_fields=["subevent_slug"])
        client.get_subevent.side_effect = RuntimeError("pull also failed")
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            with self.assertRaisesMessage(RuntimeError, "push failed"):
                self.item.push()


# ---------------------------------------------------------------------------
# PretixSyncItem.compute_drift()
# ---------------------------------------------------------------------------

class PretixSyncItemComputeDriftTest(_PretixSyncItemTestBase):

    def test_none_when_not_pushed(self):
        self.assertIsNone(self.item.compute_drift())

    def test_none_when_pushed_but_not_pulled(self):
        self.item.subevent_slug = "7"
        self.item.save(update_fields=["subevent_slug"])
        self.assertIsNone(self.item.compute_drift())

    def _pulled(self, **subevent_overrides):
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "date_from": DEFAULT_PAYLOAD["start"],
                "date_to": DEFAULT_PAYLOAD["end"],
                "name": {"de": DEFAULT_PAYLOAD["title"]},
                **subevent_overrides,
            },
            "quotas": [{"id": 1, "size": DEFAULT_PAYLOAD["max_participants"]}],
            "items": [],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])

    def test_empty_when_in_sync(self):
        self._pulled()
        diff = self.item.compute_drift()
        self.assertEqual(diff.properties, [])
        self.assertEqual(diff.entity_id, str(self.entity.id))
        self.assertEqual(diff.target_key, "pretix:main")

    def test_detects_name_difference(self):
        self._pulled(name={"de": "Old Name"})
        diff = self.item.compute_drift()
        names = {p.property_name for p in diff.properties}
        self.assertIn("name", names)

    def test_detects_quota_size_difference(self):
        self.item.subevent_slug = "7"
        self.item.pretix_data = {
            "subevent": {
                "date_from": DEFAULT_PAYLOAD["start"], "date_to": DEFAULT_PAYLOAD["end"],
                "name": {"de": DEFAULT_PAYLOAD["title"]},
            },
            "quotas": [{"id": 1, "size": 5}],
            "items": [],
        }
        self.item.save(update_fields=["subevent_slug", "pretix_data"])
        diff = self.item.compute_drift()
        names = {p.property_name for p in diff.properties}
        self.assertIn("quota_size", names)

    def test_timezone_equivalent_dates_treated_as_equal(self):
        self._pulled(date_from="2026-05-01T08:00:00+00:00")  # same instant as +02:00 10:00
        diff = self.item.compute_drift()
        names = {p.property_name for p in diff.properties}
        self.assertNotIn("date_from", names)


# ---------------------------------------------------------------------------
# PretixSyncItem.delete_remote() / allowed_statuses()
# ---------------------------------------------------------------------------

class PretixSyncItemMiscTest(_PretixSyncItemTestBase):

    def test_delete_remote_no_op_without_subevent(self):
        with patch("sync_pretix.models.PretixApiClient") as mock_cls:
            self.item.delete_remote()
        mock_cls.assert_not_called()

    def test_delete_remote_clears_subevent(self):
        self.item.subevent_slug = "7"
        self.item.pretix_data = {"subevent": {}}
        self.item.save(update_fields=["subevent_slug", "pretix_data"])
        client = MagicMock()
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.delete_remote()
        client.delete_subevent.assert_called_once()
        self.item.refresh_from_db()
        self.assertIsNone(self.item.subevent_slug)
        self.assertIsNone(self.item.pretix_data)

    def test_allowed_statuses_includes_cancelled(self):
        self.assertIn("cancelled", PretixSyncItem.allowed_statuses())
        self.assertTrue(PretixSyncItem.BASE_STATUSES.issuperset(SyncBaseItem.BASE_STATUSES))

    def test_item_admin_url_none_without_subevent(self):
        self.assertIsNone(self.item.item_admin_url)

    def test_item_admin_url_built_from_association(self):
        self.item.subevent_slug = "7"
        self.item.save(update_fields=["subevent_slug"])
        url = self.item.item_admin_url
        self.assertIn("area-metal", url)
        self.assertIn("subevents/7", url)


# ---------------------------------------------------------------------------
# PretixPricingConfiguration / CalculatedPrices — untouched by this port
# (still apiv1.Event-linked; out of scope for the sync-framework relocation).
# ---------------------------------------------------------------------------

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

    def test_min_participants_thresholds_are_sorted_and_normalized(self):
        self.config.min_participants_params = {"7": "2", "0": "1"}
        self.config.save(update_fields=["min_participants_params"])
        self.assertEqual(self.config.min_participants_thresholds, [(0, 1), (7, 2)])

    def test_calculated_prices_match_documentation_example(self):
        prices = self.config.get_calculated_prices(
            duration_hours=1.5, material_cost=3.0, max_participants=8, is_basic_course=True,
        )
        self.assertEqual(prices.member_regular_gross_eur, Decimal("17.00"))
        self.assertEqual(prices.member_discounted_gross_eur, Decimal("16.00"))
        self.assertEqual(prices.guest_regular_gross_eur, Decimal("20.00"))
        self.assertEqual(prices.guest_discounted_gross_eur, Decimal("17.00"))
        self.assertEqual(prices.business_net_eur, Decimal("32.00"))

    def test_save_populates_empty_price_fields_from_linked_event_proposal(self):
        prices = CalculatedPrices.objects.create(event=self.event)
        self.assertEqual(prices.duration_hours, Decimal("3"))
        self.assertEqual(prices.pricing_configuration, self.config)

    def test_save_uses_explicit_pricing_configuration(self):
        custom = PretixPricingConfiguration.objects.create(lecturer_rate=200)
        prices = CalculatedPrices.objects.create(event=self.event, pricing_configuration=custom)
        self.assertEqual(prices.pricing_configuration, custom)

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
            event=self.event, member_regular_gross_eur=Decimal("999.00"),
        )
        self.assertEqual(prices.member_regular_gross_eur, Decimal("999.00"))

    def test_event_without_proposal_raises_validation_error(self):
        now = timezone.now()
        event = Event.objects.create(
            series=self.series, proposal=None, name="No Proposal", start_time=now, end_time=now,
        )
        prices = CalculatedPrices(event=event)
        with self.assertRaises(ValidationError):
            prices.save()
