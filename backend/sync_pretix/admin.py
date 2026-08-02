from django.contrib import admin
from polymorphic.admin import PolymorphicChildModelAdmin
from simple_history.admin import SimpleHistoryAdmin

from project.admin_utils import MaskedSecretFormMixin
from sync_pretix import models


class CalculatedPricesInline(admin.TabularInline):
	model = models.CalculatedPrices
	extra = 0
	fields = (
		"event",
		"member_regular_gross_eur",
		"member_discounted_gross_eur",
		"guest_regular_gross_eur",
		"guest_discounted_gross_eur",
		"business_net_eur",
	)
	readonly_fields = ("created_at", "updated_at")


class PretixSyncTargetAreaAssociationInline(admin.StackedInline):
	model = models.PretixSyncTargetAreaAssociation
	fk_name = "sync_target"
	extra = 0
	fields = (
		"area",
		"event_slug",
		"ticket_product_member_regular_id",
		"ticket_product_member_discounted_id",
		"ticket_product_guest_regular_id",
		"ticket_product_guest_discounted_id",
		"ticket_product_business_id",
	)
	raw_id_fields = ("area",)


@admin.register(models.PretixSyncTarget)
class PretixSyncTargetAdmin(MaskedSecretFormMixin, PolymorphicChildModelAdmin, SimpleHistoryAdmin):
	list_display = ("organizer_slug", "api_url", "created_at", "updated_at")
	search_fields = ("organizer_slug", "api_url")
	ordering = ("-updated_at",)
	inlines = (PretixSyncTargetAreaAssociationInline,)
	# Mask the Pretix API token on display; blank-on-save keeps the existing value.
	secret_fields = ("api_token",)


@admin.register(models.PretixSyncTargetAreaAssociation)
class PretixSyncTargetAreaAssociationAdmin(SimpleHistoryAdmin):
	list_display = ("area", "event_slug", "created_at", "updated_at")
	list_filter = ("area",)
	search_fields = ("area__code", "area__label", "event_slug")
	ordering = ("area__sort_order", "area__label")


@admin.register(models.PretixPricingConfiguration)
class PretixPricingConfigurationAdmin(SimpleHistoryAdmin):
	list_display = (
		"id",
		"prep_hours",
		"lecturer_rate",
		"workshop_rate_basis",
		"workshop_rate_regular",
		"created_at",
		"updated_at",
	)
	inlines = (CalculatedPricesInline,)
	ordering = ("-updated_at",)


@admin.register(models.CalculatedPrices)
class CalculatedPricesAdmin(SimpleHistoryAdmin):
	list_display = (
		"event",
		"pricing_configuration",
		"member_regular_gross_eur",
		"guest_regular_gross_eur",
		"business_net_eur",
		"updated_at",
	)
	list_filter = ("pricing_configuration",)
	search_fields = ("event__name", "event__proposal__title")
	raw_id_fields = ("event", "pricing_configuration")


@admin.register(models.PretixSyncItem)
class PretixSyncItemAdmin(SimpleHistoryAdmin):
	list_display = ("sync_target", "area_association", "subevent_slug", "flag_push", "updated_at")
	list_filter = ("sync_target", "flag_push")
	search_fields = ("sync_target__organizer_slug", "area_association__event_slug", "subevent_slug")
	ordering = ("-updated_at",)