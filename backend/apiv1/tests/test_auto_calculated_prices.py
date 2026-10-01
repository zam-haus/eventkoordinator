from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from apiv1.models.basedata import Proposal, Series, SubmissionType
from sync_pretix.models import CalculatedPrices


class AutoCalculatedPricesOnEventCreationTest(TestCase):
    """The submission type flag decides whether event creation generates prices."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="auto-prices-user",
            password="pw-123",
            email="auto-prices-user@example.com",
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(
                codename__in=["add_event", "change_series", "add_calculatedprices"]
            )
        )
        self.series = Series.objects.create(name="Auto Prices Series")

    def _make_proposal(self, *, auto: bool | None) -> Proposal:
        submission_type = None
        if auto is not None:
            submission_type = SubmissionType.objects.create(
                code=f"auto-{auto}",
                label=f"Auto {auto}",
                auto_calculate_prices=auto,
            )
        return Proposal.objects.create(
            title="Auto Prices Workshop",
            abstract="A" * 60,
            description="B" * 120,
            material_cost_eur=Decimal("4.50"),
            preferred_dates="Any",
            duration_days=2,
            duration_time_per_day="01:30",
            max_participants=8,
            is_basic_course=True,
            submission_type=submission_type,
        )

    def _create_event(self, proposal: Proposal | None) -> str:
        self.client.force_login(self.user)
        body = {"name": "Auto Prices Event"}
        if proposal is not None:
            body["proposal_id"] = str(proposal.id)
        response = self.client.post(
            f"/api/v1/series/{self.series.id}/events/create",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["event"]["id"]

    def test_flag_enabled_creates_prices(self) -> None:
        event_id = self._create_event(self._make_proposal(auto=True))

        prices = CalculatedPrices.objects.get(event_id=event_id)
        self.assertIsNotNone(prices.pricing_configuration)
        self.assertIsNotNone(prices.member_regular_gross_eur)
        self.assertIsNotNone(prices.guest_regular_gross_eur)
        self.assertIsNotNone(prices.business_net_eur)

    def test_flag_disabled_creates_no_prices(self) -> None:
        event_id = self._create_event(self._make_proposal(auto=False))

        self.assertFalse(CalculatedPrices.objects.filter(event_id=event_id).exists())

    def test_proposal_without_submission_type_creates_no_prices(self) -> None:
        event_id = self._create_event(self._make_proposal(auto=None))

        self.assertFalse(CalculatedPrices.objects.filter(event_id=event_id).exists())

    def test_event_without_proposal_creates_no_prices(self) -> None:
        event_id = self._create_event(None)

        self.assertFalse(CalculatedPrices.objects.filter(event_id=event_id).exists())
