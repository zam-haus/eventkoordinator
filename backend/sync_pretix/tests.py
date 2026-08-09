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


BINDINGS_PAYLOAD = {
    "title": "Workshop",
    "start": "2026-05-01T10:00:00+02:00",
    "end": "2026-05-01T12:00:00+02:00",
    "locale": "de",
    "max_participants": 10,
    "parent_event": "area-metal",
    "items": [
        {"item": "Regular", "variation": None, "price": "17.00"},
        # A resolved price of None (e.g. an effective key the policy didn't
        # produce) still counts as a quota member — price is a required
        # binding in the schema, but its *resolved value* can still be None.
        {"item": "2", "variation": "Student", "price": None},
    ],
}

ITEMS_WITH_VARIATIONS = [
    {"id": 1, "name": {"de": "Regular"}, "variations": []},
    {
        "id": 2, "name": {"de": "Discounted"},
        "variations": [{"id": 10, "value": {"de": "Student"}}],
    },
]


class _PretixSyncItemTestBase(TestCase):
    """Shared setUp for PretixSyncItem tests: a UDM entity stands in for the
    "event" (events-and-sync.md §3, Step 11 — items relate to a generic
    UserDefinedModelEntity, not apiv1.models.Event), and `synced_payload`
    stands in for the effective-values snapshot mark_sync would take. Every
    entry point (parent_event resolution, item/variation bindings) is
    configured via the `sync_pretix` type-editor tab's binding config —
    events-and-sync.md §14 — there is no per-target admin-configured
    area association anymore."""

    databases = ["default"]

    def setUp(self):
        self.entity, *_ = make_entity_with_type()
        self.target = PretixSyncTarget.objects.create(
            key="pretix:main", name="Pretix",
            api_token="test-token",
            api_url="https://pretix.example.com/api/v1",
            organizer_slug="zam",
        )
        self.item = PretixSyncItem.objects.create(
            sync_target=self.target,
            related_entity=self.entity,
            synced_payload=dict(BINDINGS_PAYLOAD),
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
        self.item.remote_identity = {"organizer_slug": "zam", "event_slug": "area-metal", "subevent_id": "7"}
        self.item.save(update_fields=["subevent_slug", "remote_identity"])

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

    def test_pull_update_raises_when_no_remote_identity(self):
        self.item.subevent_slug = "7"
        self.item.save(update_fields=["subevent_slug"])
        with self.assertRaises(ValueError):
            self.item.pull_update()


# ---------------------------------------------------------------------------
# PretixSyncItem.push()
# ---------------------------------------------------------------------------

class PretixSyncItemPushTest(_PretixSyncItemTestBase):

    def test_push_skips_when_parent_event_unresolved_and_no_subevent_yet(self):
        """events-and-sync.md §14: 'save must always be possible' means an
        unconfigured/unresolved parent_event is not an error — push() simply
        has nothing to do yet."""
        self.item.synced_payload = {**BINDINGS_PAYLOAD, "parent_event": None}
        self.item.save(update_fields=["synced_payload"])
        with patch("sync_pretix.models.PretixApiClient") as mock_cls:
            self.item.push()
        mock_cls.assert_not_called()
        self.item.refresh_from_db()
        self.assertIsNone(self.item.subevent_slug)

    def test_push_creates_subevent_on_first_push(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        self.item.refresh_from_db()
        self.assertEqual(self.item.subevent_slug, "7")
        client.create_subevent.assert_called_once()
        create_payload = client.create_subevent.call_args.kwargs["payload"]
        self.assertEqual(create_payload["date_from"], BINDINGS_PAYLOAD["start"])
        self.assertEqual(create_payload["date_to"], BINDINGS_PAYLOAD["end"])
        self.assertEqual(create_payload["name"]["de"], "Workshop")

    def test_push_patches_existing_subevent_without_recreating(self):
        self.item.subevent_slug = "7"
        self.item.remote_identity = {"organizer_slug": "zam", "event_slug": "area-metal", "subevent_id": "7"}
        self.item.save(update_fields=["subevent_slug", "remote_identity"])
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        client.create_subevent.assert_not_called()
        client.patch_subevent.assert_called_once()

    def test_push_creates_quota_with_max_participants(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        client.list_quotas.side_effect = [[], [{"id": 1, "size": 10, "items": []}]]
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        client.create_quota.assert_called_once()
        quota_payload = client.create_quota.call_args.kwargs["payload"]
        self.assertEqual(quota_payload["size"], 10)

    def test_push_wraps_api_error_and_still_pulls(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        client.get_event.side_effect = RuntimeError("boom")
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            with self.assertRaises(RuntimeError):
                self.item.push()
        # pull_update() is attempted in the finally block even on failure;
        # with no subevent_slug set yet it's a no-op, so get_subevent is not called.
        client.get_subevent.assert_not_called()

    def test_push_pulls_after_successful_push(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()
        client.get_subevent.assert_called_once()

    def test_push_pull_failure_does_not_mask_original_exception(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        client.get_event.side_effect = RuntimeError("push failed")
        self.item.subevent_slug = "7"
        self.item.remote_identity = {"organizer_slug": "zam", "event_slug": "area-metal", "subevent_id": "7"}
        self.item.save(update_fields=["subevent_slug", "remote_identity"])
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
        self.item.remote_identity = {"organizer_slug": "zam", "event_slug": "area-metal", "subevent_id": "7"}
        self.item.pretix_data = {
            "subevent": {
                "date_from": BINDINGS_PAYLOAD["start"],
                "date_to": BINDINGS_PAYLOAD["end"],
                "name": {"de": BINDINGS_PAYLOAD["title"]},
                **subevent_overrides,
            },
            "quotas": [{"id": 1, "size": BINDINGS_PAYLOAD["max_participants"]}],
            "items": [],
        }
        self.item.save(update_fields=["subevent_slug", "remote_identity", "pretix_data"])

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
        self._pulled()
        self.item.pretix_data["quotas"] = [{"id": 1, "size": 5}]
        self.item.save(update_fields=["pretix_data"])
        diff = self.item.compute_drift()
        names = {p.property_name for p in diff.properties}
        self.assertIn("quota_size", names)

    def test_timezone_equivalent_dates_treated_as_equal(self):
        self._pulled(date_from="2026-05-01T08:00:00+00:00")  # same instant as +02:00 10:00
        diff = self.item.compute_drift()
        names = {p.property_name for p in diff.properties}
        self.assertNotIn("date_from", names)

    def test_surfaces_parent_event_mismatch(self):
        """events-and-sync.md §14: the crux of identity pinning — a later
        change to what parent_event resolves to must show up as drift, not
        move the subevent (see PretixSyncItemPushTest for the push-side
        assertion that it doesn't move)."""
        self._pulled()
        self.item.synced_payload = {**BINDINGS_PAYLOAD, "parent_event": "area-different"}
        self.item.save(update_fields=["synced_payload"])

        diff = self.item.compute_drift()
        by_name = {p.property_name: p for p in diff.properties}
        self.assertIn("parent_event", by_name)
        self.assertEqual(by_name["parent_event"].old_value, "area-metal")
        self.assertEqual(by_name["parent_event"].new_value, "area-different")


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
        self.item.remote_identity = {"organizer_slug": "zam", "event_slug": "area-metal", "subevent_id": "7"}
        self.item.pretix_data = {"subevent": {}}
        self.item.save(update_fields=["subevent_slug", "remote_identity", "pretix_data"])
        client = MagicMock()
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.delete_remote()
        client.delete_subevent.assert_called_once_with(
            organizer_slug="zam", event_slug="area-metal", subevent_id="7",
        )
        self.item.refresh_from_db()
        self.assertIsNone(self.item.subevent_slug)
        self.assertIsNone(self.item.pretix_data)

    def test_allowed_statuses_includes_cancelled(self):
        self.assertIn("cancelled", PretixSyncItem.allowed_statuses())
        self.assertTrue(PretixSyncItem.BASE_STATUSES.issuperset(SyncBaseItem.BASE_STATUSES))

    def test_item_admin_url_none_without_subevent(self):
        self.assertIsNone(self.item.item_admin_url)

    def test_item_admin_url_built_from_remote_identity(self):
        self.item.subevent_slug = "7"
        self.item.remote_identity = {"organizer_slug": "zam", "event_slug": "area-metal", "subevent_id": "7"}
        self.item.save(update_fields=["subevent_slug", "remote_identity"])
        url = self.item.item_admin_url
        self.assertIn("area-metal", url)
        self.assertIn("subevents/7", url)


# ---------------------------------------------------------------------------
# PretixSyncItem.push() — item/variation bindings (events-and-sync.md §14)
# ---------------------------------------------------------------------------

class PretixSyncItemBindingsPushTest(_PretixSyncItemTestBase):
    """Parent event resolved dynamically + item/variation bindings pinning
    remote identity."""

    def test_push_creates_subevent_and_pins_remote_identity(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        self.item.refresh_from_db()
        self.assertEqual(self.item.subevent_slug, "7")
        self.assertEqual(self.item.remote_identity, {
            "organizer_slug": "zam", "event_slug": "area-metal", "subevent_id": "7",
        })
        client.create_subevent.assert_called_once_with(
            organizer_slug="zam", event_slug="area-metal", payload=client.create_subevent.call_args.kwargs["payload"],
        )

    def test_push_applies_item_and_variation_price_overrides(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        push_payload = client.create_subevent.call_args.kwargs["payload"]
        self.assertEqual(push_payload["item_price_overrides"], [{"item": 1, "price": "17.00"}])
        self.assertEqual(push_payload["variation_price_overrides"], [])

    def test_push_quota_includes_items_and_variations(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        client.list_quotas.side_effect = [[], [{"id": 1, "size": 10, "items": []}]]
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()

        quota_payload = client.create_quota.call_args.kwargs["payload"]
        # Item 2 (the variation's parent) must be listed alongside item 1 —
        # Pretix rejects a quota whose variations list a variation whose
        # parent item isn't also present in items.
        self.assertEqual(quota_payload["items"], [1, 2])
        self.assertEqual(quota_payload["variations"], [10])

    def test_identity_pinned_does_not_move_on_later_parent_event_change(self):
        """The crux of §14: once remote_identity is pinned, a later change to
        what parent_event resolves to must not move the existing subevent —
        the pinned event/organizer are reused verbatim."""
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()
        self.item.refresh_from_db()
        self.assertEqual(self.item.remote_identity["event_slug"], "area-metal")

        # parent_event now resolves to a different event (e.g. entity moved
        # to a different series in policy) — but push must still target the
        # pinned identity, not the freshly-resolved one.
        self.item.synced_payload = {**BINDINGS_PAYLOAD, "parent_event": "area-different"}
        self.item.save(update_fields=["synced_payload"])
        client2 = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        with patch("sync_pretix.models.PretixApiClient", return_value=client2):
            self.item.push()

        client2.patch_subevent.assert_called_once()
        self.assertEqual(client2.patch_subevent.call_args.kwargs["event_slug"], "area-metal")
        client2.create_subevent.assert_not_called()
        self.item.refresh_from_db()
        self.assertEqual(self.item.remote_identity["event_slug"], "area-metal")

    def test_pull_delete_use_pinned_identity(self):
        client = _make_pretix_client_mock(items=ITEMS_WITH_VARIATIONS)
        with patch("sync_pretix.models.PretixApiClient", return_value=client):
            self.item.push()
        self.item.refresh_from_db()

        client2 = MagicMock()
        with patch("sync_pretix.models.PretixApiClient", return_value=client2):
            self.item.delete_remote()
        client2.delete_subevent.assert_called_once_with(
            organizer_slug="zam", event_slug="area-metal", subevent_id="7",
        )


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


# ---------------------------------------------------------------------------
# Step 15 — rego port of PretixPricingConfiguration.get_calculated_prices()
# (documentation/configuration/policies/event.rego). Same formulas, same
# hardcoded rate constants, evaluated through the real policy engine rather
# than Python — a UDM entity's own fields drive `effective.price_*` and the
# min/max-over-timeslots `effective.start`/`effective.end`.
# ---------------------------------------------------------------------------

# The pricing + timeslot-span portion of event.rego — see event.rego for the
# identical, in-production copy. duration_hours/material_cost/max_participants
# (base)/is_basic_course are read off a linked "origin" entity (standing in
# for Proposal) rather than duplicated as Event fields — they already live
# there in the real bundle.
_PRICING_POLICY_BODY = """
package udm
import rego.v1

default allow := false
allow if input.action == "view"

linked_inputs contains "origin"

_timeslot_children := object.get(input.entity.children, "timeslots", [])

_timeslot_starts_ns := {ns |
	some child in _timeslot_children
	raw := child.fields.start.value
	raw != null
	ns := time.parse_rfc3339_ns(raw)
}

_timeslot_ends_ns := {ns |
	some child in _timeslot_children
	raw := child.fields.end.value
	raw != null
	ns := time.parse_rfc3339_ns(raw)
}

effective["start"] := time.format(min(_timeslot_starts_ns)) if {
	count(_timeslot_starts_ns) > 0
}

effective["end"] := time.format(max(_timeslot_ends_ns)) if {
	count(_timeslot_ends_ns) > 0
}

_prep_hours := 0
_lecturer_rate := 40
_workshop_rate_basis := 10
_workshop_rate_regular := 20
_guest_surcharge := 10
_discount_rate := 0.50
_business_surcharge := 0.75
_vat_rate := 0.07
_min_participants_params := {0: 1, 7: 2}

_duration_time_parts := split(input.linked.origin.fields["duration-time-per-day"].value, ":")

_num_stripped_zeros(s) := to_number(t) if {
	t := trim_left(s, "0")
	t != ""
}

_num_stripped_zeros(s) := 0 if trim_left(s, "0") == ""

_duration_time_minutes := (_num_stripped_zeros(_duration_time_parts[0]) * 60) + _num_stripped_zeros(_duration_time_parts[1]) if {
	count(_duration_time_parts) > 1
}

_duration_time_minutes := _num_stripped_zeros(_duration_time_parts[0]) if {
	count(_duration_time_parts) == 1
}

default _duration_time_minutes := 0

_duration := (_duration_time_minutes * to_number(input.linked.origin.fields["duration-days"].value)) / 60
_material := to_number(input.linked.origin.fields["material-cost-eur"].value)
_is_basic_course := input.linked.origin.fields["is-basic-course"].value == true

_workshop_rate := _workshop_rate_basis if _is_basic_course
_workshop_rate := _workshop_rate_regular if not _is_basic_course

effective["max_participants"] := v if {
	v := to_number(input.entity.fields.max_participants_override.value)
	input.entity.fields.max_participants_override.value != null
}

effective["max_participants"] := to_number(input.linked.origin.fields["max-participants"].value) if {
	input.entity.fields.max_participants_override.value == null
}

_max_participants := effective["max_participants"]

_min_participants_deduction := d if {
	applicable := [t | some t, _ in _min_participants_params; t <= _max_participants]
	count(applicable) > 0
	d := _min_participants_params[max(applicable)]
}

default _min_participants_deduction := 0

_min_participants_computed := max([_max_participants - _min_participants_deduction, 1])

effective["min_participants"] := v if {
	v := to_number(input.entity.fields.min_participants_override.value)
	input.entity.fields.min_participants_override.value != null
}

effective["min_participants"] := _min_participants_computed if {
	input.entity.fields.min_participants_override.value == null
}

_min_participants := effective["min_participants"]

effective["price_member_regular"] := v if {
	v := to_number(input.entity.fields.price_member_regular_override.value)
	input.entity.fields.price_member_regular_override.value != null
}

effective["price_member_regular"] := ceil(
	(_duration * (_workshop_rate + _lecturer_rate) + _lecturer_rate * _prep_hours) *
	(1 + _vat_rate) / _min_participants + _material,
) if {
	input.entity.fields.price_member_regular_override.value == null
}

effective["price_member_discounted"] := v if {
	v := to_number(input.entity.fields.price_member_discounted_override.value)
	input.entity.fields.price_member_discounted_override.value != null
}

effective["price_member_discounted"] := ceil(
	(_duration * (_workshop_rate * (1 - _discount_rate) + _lecturer_rate) + _lecturer_rate * _prep_hours) *
	(1 + _vat_rate) / _min_participants + _material,
) if {
	input.entity.fields.price_member_discounted_override.value == null
}

effective["price_guest_regular"] := v if {
	v := to_number(input.entity.fields.price_guest_regular_override.value)
	input.entity.fields.price_guest_regular_override.value != null
}

effective["price_guest_regular"] := ceil(
	(_duration * (_workshop_rate + _guest_surcharge + _lecturer_rate) + _lecturer_rate * _prep_hours) *
	(1 + _vat_rate) / _min_participants + _material,
) if {
	input.entity.fields.price_guest_regular_override.value == null
}

effective["price_guest_discounted"] := v if {
	v := to_number(input.entity.fields.price_guest_discounted_override.value)
	input.entity.fields.price_guest_discounted_override.value != null
}

effective["price_guest_discounted"] := effective["price_member_regular"] if {
	input.entity.fields.price_guest_discounted_override.value == null
}

_business_base := ceil(
	(_duration * (_workshop_rate + _guest_surcharge + _lecturer_rate) + _lecturer_rate * _prep_hours) /
	_min_participants + _material,
)

effective["price_business"] := v if {
	v := to_number(input.entity.fields.price_business_override.value)
	input.entity.fields.price_business_override.value != null
}

effective["price_business"] := ceil(_business_base * (1 + _business_surcharge)) if {
	input.entity.fields.price_business_override.value == null
}

effective["price_internal_training"] := v if {
	v := to_number(input.entity.fields.price_internal_training_override.value)
	input.entity.fields.price_internal_training_override.value != null
}

effective["price_internal_training"] := _material if {
	input.entity.fields.price_internal_training_override.value == null
}
"""


class PretixRegoPricingPolicyTests(TestCase):
    """events-and-sync.md §15: the rego port must reproduce
    PretixPricingConfigurationTests::test_calculated_prices_match_documentation_example
    numerically, and effective.start/end must be the true min/max across
    timeslot children, not the first/last slot's own start/end."""

    databases = ["default"]

    def setUp(self):
        from userdefinedmodel.models import ConfigLanguage, ConfigVersion, DataField, FieldConfig
        from userdefinedmodel.tests.factories import (
            UserDefinedModelEntityFactory, UserDefinedModelTypeFactory, UserFactory,
            wrap_policy,
        )

        self.sub_config = FieldConfig.objects.create(name="Timeslot Sub Config")
        ConfigLanguage.objects.create(config=self.sub_config, code="en", label="English", is_default=True)
        self.sub_version = ConfigVersion.objects.create(config=self.sub_config, status="published")
        self.start_field = DataField.objects.create(version=self.sub_version, slug="start", data_type="datetime")
        self.end_field = DataField.objects.create(version=self.sub_version, slug="end", data_type="datetime")

        # Proposal-stand-in: duration/material/participants/basic-course
        # live here in the real bundle (UDM_BUNDLE.json Proposal fields),
        # not duplicated onto Event — the Event links to it via "origin".
        self.proposal_config = FieldConfig.objects.create(name="Proposal-like Config")
        ConfigLanguage.objects.create(config=self.proposal_config, code="en", label="English", is_default=True)
        self.proposal_version = ConfigVersion.objects.create(config=self.proposal_config, status="published")
        self.duration_days_field = DataField.objects.create(
            version=self.proposal_version, slug="duration-days", data_type="integer",
        )
        self.duration_time_field = DataField.objects.create(
            version=self.proposal_version, slug="duration-time-per-day", data_type="text_short",
        )
        self.material_field = DataField.objects.create(
            version=self.proposal_version, slug="material-cost-eur", data_type="float",
        )
        self.max_participants_field = DataField.objects.create(
            version=self.proposal_version, slug="max-participants", data_type="integer",
        )
        self.is_basic_field = DataField.objects.create(
            version=self.proposal_version, slug="is-basic-course", data_type="boolean",
        )
        self.proposal_type = UserDefinedModelTypeFactory(name="Proposal-like Type", field_config=self.proposal_config)
        self.proposal = UserDefinedModelEntityFactory(
            config_version=self.proposal_version, user_defined_model_type=self.proposal_type,
        )

        self.config = FieldConfig.objects.create(name="Pricing Root Config")
        ConfigLanguage.objects.create(config=self.config, code="en", label="English", is_default=True)
        self.version = ConfigVersion.objects.create(config=self.config, status="published")
        self.timeslots_field = DataField.objects.create(
            version=self.version, slug="timeslots", data_type="submodel_list",
            submodel_config=self.sub_version,
        )
        self.origin_field = DataField.objects.create(version=self.version, slug="origin", data_type="entity_select")
        self.max_participants_override_field = DataField.objects.create(
            version=self.version, slug="max_participants_override", data_type="integer",
        )
        self.min_participants_override_field = DataField.objects.create(
            version=self.version, slug="min_participants_override", data_type="integer",
        )
        self.price_override_fields = {
            key: DataField.objects.create(version=self.version, slug=f"{key}_override", data_type="float")
            for key in (
                "price_member_regular", "price_member_discounted", "price_guest_regular",
                "price_guest_discounted", "price_business", "price_internal_training",
            )
        }

        self.udm_type = UserDefinedModelTypeFactory(name="Course Type", field_config=self.config)
        self.entity = UserDefinedModelEntityFactory(
            config_version=self.version, user_defined_model_type=self.udm_type,
        )

        from userdefinedmodel.models import FieldValue, Policy, UserDefinedModelTypePolicy
        policy = Policy.objects.create(slug="pricing-policy", source=wrap_policy(_PRICING_POLICY_BODY))
        UserDefinedModelTypePolicy.objects.create(
            user_defined_model_type=self.udm_type, policy=policy, sort_order=0,
        )
        FieldValue.objects.create(node=self.entity, field=self.origin_field, language="", value_node=self.proposal)
        FieldValue.objects.create(node=self.proposal, field=self.duration_days_field, language="", value_decimal="1")
        FieldValue.objects.create(node=self.proposal, field=self.duration_time_field, language="", value_text="01:30")
        FieldValue.objects.create(node=self.proposal, field=self.material_field, language="", value_decimal="3.0")
        FieldValue.objects.create(node=self.proposal, field=self.max_participants_field, language="", value_decimal="8")
        FieldValue.objects.create(node=self.proposal, field=self.is_basic_field, language="", value_bool=True)

        self.user = UserFactory()

    def _add_slot(self, start, end):
        from userdefinedmodel.models import FieldValue, UserDefinedModelEntityNode

        child = UserDefinedModelEntityNode.objects.create(
            parent_node=self.entity, parent_field=self.timeslots_field, config_version=self.sub_version,
        )
        FieldValue.objects.create(node=child, field=self.start_field, language="", value_datetime=start)
        FieldValue.objects.create(node=child, field=self.end_field, language="", value_datetime=end)
        return child

    def test_prices_match_documentation_example(self):
        from userdefinedmodel.engine import evaluate_policy

        output = evaluate_policy(self.entity, self.user, "view")

        self.assertEqual(output.effective["price_member_regular"], 17)
        self.assertEqual(output.effective["price_member_discounted"], 16)
        self.assertEqual(output.effective["price_guest_regular"], 20)
        self.assertEqual(output.effective["price_guest_discounted"], 17)
        self.assertEqual(output.effective["price_business"], 32)
        self.assertEqual(output.effective["price_internal_training"], 3.0)
        self.assertEqual(output.effective["max_participants"], 8)
        self.assertEqual(output.effective["min_participants"], 6)

    def test_price_override_wins_over_calculated_value(self):
        from userdefinedmodel.engine import evaluate_policy
        from userdefinedmodel.models import FieldValue

        FieldValue.objects.create(
            node=self.entity, field=self.price_override_fields["price_member_regular"],
            language="", value_decimal="999.00",
        )

        output = evaluate_policy(self.entity, self.user, "view")

        self.assertEqual(output.effective["price_member_regular"], 999.0)
        # Untouched keys still come from the formula.
        self.assertEqual(output.effective["price_member_discounted"], 16)

    def test_guest_discounted_override_wins_over_member_regular_reuse(self):
        from userdefinedmodel.engine import evaluate_policy
        from userdefinedmodel.models import FieldValue

        FieldValue.objects.create(
            node=self.entity, field=self.price_override_fields["price_guest_discounted"],
            language="", value_decimal="12.00",
        )

        output = evaluate_policy(self.entity, self.user, "view")

        self.assertEqual(output.effective["price_guest_discounted"], 12.0)
        self.assertEqual(output.effective["price_member_regular"], 17)

    def test_max_participants_override_reflows_min_participants_and_prices(self):
        from userdefinedmodel.engine import evaluate_policy
        from userdefinedmodel.models import FieldValue

        # 20 participants crosses no new threshold in the {0: 1, 7: 2} table
        # (still deduction=2), but the higher max_participants alone changes
        # min_participants (20 - 2 = 18) and every price derived from it.
        FieldValue.objects.create(
            node=self.entity, field=self.max_participants_override_field,
            language="", value_decimal="20",
        )

        output = evaluate_policy(self.entity, self.user, "view")

        self.assertEqual(output.effective["max_participants"], 20)
        self.assertEqual(output.effective["min_participants"], 18)
        self.assertNotEqual(output.effective["price_member_regular"], 17)

    def test_min_participants_override_wins_over_computed_deduction_and_reflows_prices(self):
        from userdefinedmodel.engine import evaluate_policy
        from userdefinedmodel.models import FieldValue

        FieldValue.objects.create(
            node=self.entity, field=self.min_participants_override_field,
            language="", value_decimal="1",
        )

        output = evaluate_policy(self.entity, self.user, "view")

        self.assertEqual(output.effective["max_participants"], 8)
        self.assertEqual(output.effective["min_participants"], 1)
        self.assertNotEqual(output.effective["price_member_regular"], 17)

    def test_start_end_are_true_min_max_across_timeslots_not_first_last(self):
        import datetime

        from userdefinedmodel.engine import evaluate_policy

        # Deliberately out-of-order: the middle slot (by creation order) has
        # the earliest start, the first-created slot has neither the min nor
        # the max — a first/last-slot implementation would get this wrong.
        self._add_slot(
            datetime.datetime(2026, 6, 16, 9, 0, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 6, 16, 10, 0, tzinfo=datetime.timezone.utc),
        )
        self._add_slot(
            datetime.datetime(2026, 6, 15, 8, 0, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc),
        )
        self._add_slot(
            datetime.datetime(2026, 6, 17, 12, 0, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 6, 17, 14, 0, tzinfo=datetime.timezone.utc),
        )

        output = evaluate_policy(self.entity, self.user, "view")

        self.assertEqual(output.effective["start"], "2026-06-15T08:00:00Z")
        self.assertEqual(output.effective["end"], "2026-06-17T14:00:00Z")


class PretixRegoPricingBindingsIntegrationTest(TestCase):
    """effective.price_* keys resolved above feed straight into sync_pretix's
    `items` bindings (§14) — this is the bundle's actual wiring: five
    Kursbuchung (item 164) variations + one Kursbuchung Unternehmen (item
    165) entry, each price bound to an `effective.price_*` key."""

    def test_resolve_bindings_produces_five_variations_plus_business_item(self):
        from sync_core.binding import BindingSource, resolve_bindings
        from sync_pretix.type_editor_tab import PretixItemBinding

        effective = {
            "price_member_regular": 20, "price_member_discounted": 17,
            "price_guest_regular": 20, "price_guest_discounted": 17,
            "price_business": 32, "price_internal_training": 3.0,
        }
        items = [
            PretixItemBinding(item="164", variation="Standard", price=BindingSource(effective="price_guest_regular")),
            PretixItemBinding(item="164", variation="Ermäßigt", price=BindingSource(effective="price_guest_discounted")),
            PretixItemBinding(item="164", variation="Standard Mitglied", price=BindingSource(effective="price_member_regular")),
            PretixItemBinding(item="164", variation="Ermäßigt Mitglied", price=BindingSource(effective="price_member_discounted")),
            PretixItemBinding(item="164", variation="Interne Fortbildung", price=BindingSource(effective="price_internal_training")),
            PretixItemBinding(item="165", variation=None, price=BindingSource(effective="price_business")),
        ]

        resolved_prices = [
            resolve_bindings({"price": binding.price}, entity=None, effective=effective)["price"]
            for binding in items
        ]

        self.assertEqual(resolved_prices, [20, 17, 20, 17, 3.0, 32])
        self.assertEqual({(b.item, b.variation) for b in items}, {
            ("164", "Standard"), ("164", "Ermäßigt"), ("164", "Standard Mitglied"),
            ("164", "Ermäßigt Mitglied"), ("164", "Interne Fortbildung"), ("165", None),
        })
