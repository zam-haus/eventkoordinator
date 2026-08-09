import time

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sync_pretix import models
from sync_pretix.admin import CalculatedPricesInline


class SyncPretixAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()

    def test_models_are_registered_in_admin(self):
        self.assertIn(models.PretixSyncTarget, admin.site._registry)
        self.assertIn(models.PretixPricingConfiguration, admin.site._registry)
        self.assertIn(models.CalculatedPrices, admin.site._registry)
        self.assertIn(models.PretixSyncItem, admin.site._registry)

    def test_pricing_configuration_admin_has_calculated_prices_inline(self):
        pricing_admin = admin.site._registry[models.PretixPricingConfiguration]
        self.assertIn(CalculatedPricesInline, pricing_admin.inlines)

    def test_admin_pages_are_accessible(self):
        config = models.PretixPricingConfiguration.objects.create()
        target = models.PretixSyncTarget.objects.create(
            key="pretix:main", name="Pretix", organizer_slug="zam",
            api_url="https://pretix.example.com/api/v1", api_token="secret",
        )

        config_url = reverse(
            "admin:sync_pretix_pretixpricingconfiguration_change", args=[config.pk]
        )
        target_url = reverse(
            "admin:sync_pretix_pretixsynctarget_change", args=[target.pk]
        )

        self.assertEqual(self.client.get(config_url).status_code, 200)
        self.assertEqual(self.client.get(target_url).status_code, 200)
