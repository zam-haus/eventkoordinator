from django.db import models
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from dataclasses import dataclass
from datetime import datetime, timezone as _tz
from decimal import Decimal, ROUND_CEILING
from typing import ClassVar
import logging
import re

from apiv1.models.basedata import time_string_to_minutes
from project.basemodels import HistoricalMetaBase
from sync_core.models import PropertyDiff, SyncBaseItem, SyncBaseTarget, SyncDiffData
from sync_pretix.pretix_client import PretixApiClient, PretixApiError

logger = logging.getLogger(__name__)


def _isoformat_equal(a: str, b: str) -> bool:
    """Return True when *a* and *b* represent the same point in time.

    Handles timezone-aware and naive ISO 8601 strings. Falls back to a plain
    string comparison when parsing fails.
    """
    if a == b:
        return True
    try:
        dt_a = datetime.fromisoformat(a)
        dt_b = datetime.fromisoformat(b)
        if dt_a.tzinfo is not None and dt_b.tzinfo is not None:
            return dt_a.astimezone(_tz.utc) == dt_b.astimezone(_tz.utc)
        if dt_a.tzinfo is None and dt_b.tzinfo is None:
            return dt_a == dt_b
    except (ValueError, TypeError):
        pass
    return False


def default_min_participants_params():
    """
    Default parameters for minimum participants calculation.
    Format: {threshold: deduction}
    Logic: max_participants - deduction where threshold <= max_participants
    Example: {0: 1, 7: 2} means deduct 1 for 1-6 participants, deduct 2 for 7+ participants
    """
    return {0: 1, 7: 2}


class PretixSyncTarget(SyncBaseTarget):
    secret_field_names: ClassVar[list[str]] = ["api_token"]

    api_token = models.CharField(
        max_length=255,
        verbose_name="Pretix API Token",
        help_text="API Token for authenticating with the Pretix API",
    )
    api_url = models.CharField(
        max_length=255, verbose_name="Pretix API URL", help_text="Pretix API URL"
    )
    organizer_slug = models.CharField(
        max_length=255, verbose_name="Organizer Slug", help_text="Organizer Slug"
    )


class PretixSyncTargetAreaAssociation(HistoricalMetaBase):
    """Which Pretix event slug (+ ticket product mapping) a PretixSyncItem
    pushes into. Previously keyed by a FK to apiv1.ProposalArea; decoupled to
    a plain `area_code` string (events-and-sync.md §3, Step 11) since items
    are now generic UDM entities, not apiv1 Events with a proposal.area.
    `area_code` is set manually (e.g. matching the originating type's own
    area/category field, if it has one) — there is no automatic derivation."""

    sync_target = models.ForeignKey(
        PretixSyncTarget,
        on_delete=models.CASCADE,
        related_name="area_associations",
        null=True,
        blank=True,
    )
    area_code = models.CharField(
        max_length=100,
        verbose_name="Area code",
        help_text="Arbitrary key used to pick this association on a PretixSyncItem.",
    )
    event_slug = models.CharField(
        max_length=255, verbose_name="Event Slug", help_text="Event Slug"
    )
    ticket_product_member_regular_id = models.CharField(
        max_length=255,
        default="Regular Member Ticket",
        null=True,
        blank=True,
        verbose_name="ID or Name of Ticket Product for Members (regular)",
    )
    ticket_product_member_discounted_id = models.CharField(
        max_length=255,
        default="Discounted Member Ticket",
        null=True,
        blank=True,
        verbose_name="ID or Name of Ticket Product for Members (discounted)",
    )
    ticket_product_guest_regular_id = models.CharField(
        max_length=255,
        default="Regular Guest Ticket",
        null=True,
        blank=True,
        verbose_name="ID or Name of Ticket Product for Guests (regular)",
    )
    ticket_product_guest_discounted_id = models.CharField(
        max_length=255,
        default="Discounted Guest Ticket",
        null=True,
        blank=True,
        verbose_name="ID or Name of Ticket Product for Guests (discounted)",
    )
    ticket_product_business_id = models.CharField(
        max_length=255,
        default="Business Ticket",
        null=True,
        blank=True,
        verbose_name="ID of Ticket Product for Businesses",
    )
    ticket_product_internal_training_id = models.CharField(
        max_length=255,
        default="Interne Fortbildung",
        null=True,
        blank=True,
        verbose_name="ID or Name of Ticket Product for Internal Training",
    )


class PretixSyncItem(SyncBaseItem):
    """Links a Pretix subevent to a UDM entity via a PretixSyncTarget.

    Pushes from `self.synced_payload` (the effective-values snapshot taken by
    mark_sync, events-and-sync.md §4.2), not from a live model relation.
    Documented synced_payload shape this reads:
        {"title": str, "start": iso-string, "end": iso-string,
         "locale": str (default "de"), "max_participants": int | None,
         "prices": {"member_regular": "12.00", "member_discounted": ...,
                     "guest_regular": ..., "guest_discounted": ...,
                     "business": ..., "internal_training": ...}}
    All keys are optional; missing ones default sanely (empty title, no
    price overrides, no quota size cap change).
    """

    BASE_STATUSES: ClassVar[frozenset[str]] = SyncBaseItem.BASE_STATUSES | {"cancelled"}

    PRICE_PROPERTY_MAP: ClassVar[list[tuple[str, str]]] = [
        ("member_regular", "ticket_product_member_regular_id"),
        ("member_discounted", "ticket_product_member_discounted_id"),
        ("guest_regular", "ticket_product_guest_regular_id"),
        ("guest_discounted", "ticket_product_guest_discounted_id"),
        ("business", "ticket_product_business_id"),
        ("internal_training", "ticket_product_internal_training_id"),
    ]

    # sync_target is inherited as-is from SyncBaseItem (a plain FK to the
    # polymorphic SyncBaseTarget base) — same pattern as sync_caldav/sync_webhook.
    area_association = models.ForeignKey(
        "PretixSyncTargetAreaAssociation",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="sync_items",
        verbose_name="Area Association",
        help_text="The area-to-event-slug mapping used when pushing to Pretix.",
    )
    subevent_slug = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Pretix Subevent ID",
        help_text="ID of the Pretix subevent created for this item. Set on first push.",
    )
    pretix_data = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Pretix Subevent Data",
        help_text=(
            "Latest subevent and quota data fetched from Pretix after the most recent push. "
            "Structure: {\"subevent\": {...}, \"quotas\": [...], \"items\": [...]}"
        ),
    )

    def __str__(self):
        return f"PretixSyncItem(target={self.sync_target_id}, entity={self.related_entity_id})"

    @classmethod
    def allowed_statuses(cls) -> frozenset[str]:
        return cls.BASE_STATUSES

    @property
    def item_admin_url(self) -> str | None:
        """Return the Pretix admin URL for the linked subevent.

        Strips ``/api/v1`` (with optional trailing slash) from the configured
        ``api_url`` to derive the Pretix server base URL, then constructs the
        admin subevent URL.

        Returns ``None`` when no subevent has been created yet.
        """
        if not self.subevent_slug:
            return None
        association = self.area_association
        target = self.sync_target
        if association is None or target is None:
            return None
        server_url = re.sub(r"/api/v1/?$", "", target.api_url.rstrip("/"))
        return (
            f"{server_url}/control/event/{target.organizer_slug}"
            f"/{association.event_slug}/subevents/{self.subevent_slug}/"
        )

    def delete_remote(self) -> None:
        """Delete the linked Pretix subevent and reset the stored subevent ID."""
        if not self.subevent_slug:
            logger.info(
                "PretixSyncItem %s: delete_remote() skipped (no subevent_slug).", self.pk
            )
            return  # Nothing to delete remotely.

        association = self.area_association
        if association is None:
            raise ValueError(
                f"PretixSyncItem {self.pk} has no area association; cannot delete remote subevent."
            )

        target = self.sync_target
        client = PretixApiClient(api_base_url=target.api_url, token=target.api_token)
        logger.info(
            "PretixSyncItem %s: deleting remote subevent %s (event %s/%s).",
            self.pk, self.subevent_slug, association.event_slug, target.organizer_slug,
        )
        client.delete_subevent(
            organizer_slug=target.organizer_slug,
            event_slug=association.event_slug,
            subevent_id=self.subevent_slug,
        )
        logger.info(
            "PretixSyncItem %s: deleted Pretix subevent %s for entity %s.",
            self.pk, self.subevent_slug, self.related_entity_id,
        )
        self.subevent_slug = None
        self.pretix_data = None
        self.save(update_fields=["subevent_slug", "pretix_data", "updated_at"])

    def pull_update(self) -> None:
        """Fetch the current subevent and its quotas/items from Pretix and store in pretix_data."""
        if not self.subevent_slug:
            logger.info(
                "PretixSyncItem %s: pull_update() skipped (no subevent_slug).", self.pk
            )
            return

        association = self.area_association
        if association is None:
            raise ValueError(
                f"PretixSyncItem {self.pk} has no area association; cannot pull."
            )

        target = self.sync_target
        client = PretixApiClient(api_base_url=target.api_url, token=target.api_token)

        logger.info(
            "PretixSyncItem %s: pulling subevent %s from %s/%s.",
            self.pk, self.subevent_slug, target.organizer_slug, association.event_slug,
        )
        subevent = client.get_subevent(
            organizer_slug=target.organizer_slug,
            event_slug=association.event_slug,
            subevent_id=self.subevent_slug,
        )
        quotas = client.list_quotas(
            organizer_slug=target.organizer_slug,
            event_slug=association.event_slug,
            subevent_id=self.subevent_slug,
        )
        items = client.list_items(
            organizer_slug=target.organizer_slug,
            event_slug=association.event_slug,
        )

        self.pretix_data = {
            "subevent": subevent,
            "quotas": quotas,
            "items": items,
        }
        self.save(update_fields=["pretix_data", "updated_at"])
        logger.info(
            "PretixSyncItem %s: pull complete – stored subevent + %d quota(s) + %d item(s).",
            self.pk, len(quotas), len(items),
        )

    def compute_drift(self) -> SyncDiffData | None:
        """Compare the remote pretix_data (from the last pull) against the
        local synced_payload snapshot — remote drift caused by manual edits
        in the Pretix UI, distinct from staleness (which compares
        synced_payload against a *fresh* effective evaluation, see
        sync_core.models.recompute_staleness). None when there is nothing to
        compare yet (not pushed, or pushed but never pulled)."""
        if not self.subevent_slug or self.pretix_data is None:
            return None

        payload = self.synced_payload or {}
        stored_subevent = self.pretix_data.get("subevent") or {}
        stored_quotas = self.pretix_data.get("quotas") or []
        locale = payload.get("locale", "de")

        properties: list[PropertyDiff] = []

        expected_date_from = payload.get("start")
        actual_date_from = stored_subevent.get("date_from", "")
        if expected_date_from is not None and not _isoformat_equal(expected_date_from, actual_date_from):
            properties.append(PropertyDiff(
                property_name="date_from", old_value=expected_date_from, new_value=actual_date_from,
            ))

        expected_date_to = payload.get("end")
        actual_date_to = stored_subevent.get("date_to", "")
        if expected_date_to is not None and not _isoformat_equal(expected_date_to, actual_date_to):
            properties.append(PropertyDiff(
                property_name="date_to", old_value=expected_date_to, new_value=actual_date_to,
            ))

        expected_title = payload.get("title", "")
        actual_name = (stored_subevent.get("name") or {}).get(locale, "")
        if expected_title != actual_name:
            properties.append(PropertyDiff(
                property_name="name", old_value=expected_title, new_value=actual_name,
            ))

        expected_size = payload.get("max_participants")
        if expected_size is not None and stored_quotas:
            actual_size = stored_quotas[0].get("size")
            if actual_size != int(expected_size):
                properties.append(PropertyDiff(
                    property_name="quota_size", old_value=expected_size, new_value=actual_size,
                ))

        return SyncDiffData(
            entity_id=str(self.related_entity_id),
            target_key=self.sync_target.key if self.sync_target_id else None,
            properties=properties,
        )

    def push(self) -> None:
        """Create or update the linked Pretix subevent from synced_payload,
        applying ticket price overrides. A ``pull_update()`` is performed in
        a ``finally`` block so ``pretix_data`` (and therefore drift
        detection) is always refreshed, even when the push itself fails."""
        association = self.area_association
        if association is None:
            raise RuntimeError(
                f"PretixSyncItem {self.pk} has no area association; cannot push."
            )

        target = self.sync_target
        payload = self.synced_payload or {}
        title = payload.get("title", "")
        start = payload.get("start")
        end = payload.get("end")
        locale = payload.get("locale") or "de"
        max_participants = payload.get("max_participants")
        prices = payload.get("prices") or {}

        logger.info(
            "PretixSyncItem %s: starting push for entity %s (subevent_slug=%s, "
            "organizer=%s, event_slug=%s).",
            self.pk, self.related_entity_id, self.subevent_slug,
            target.organizer_slug, association.event_slug,
        )
        client = PretixApiClient(api_base_url=target.api_url, token=target.api_token)

        try:
            pretix_event = client.get_event(
                organizer_slug=target.organizer_slug,
                event_slug=association.event_slug,
            )
            configured_locales = pretix_event.get("locales") or [locale]
            name_dict = {loc: title for loc in configured_locales}
            name_dict[locale] = title

            items = client.list_items(
                organizer_slug=target.organizer_slug,
                event_slug=association.event_slug,
            )

            item_overrides = self._build_item_overrides(association, prices, items)
            if prices and not item_overrides:
                logger.warning(
                    "PretixSyncItem %s: prices present in synced_payload but produced no "
                    "item overrides. Check that ticket product names/ids match Pretix items.",
                    self.pk,
                )

            push_payload = {
                "name": name_dict,
                "date_from": start,
                "date_to": end,
                "active": True,
                "meta_data": {},
                "item_price_overrides": item_overrides,
            }

            if not self.subevent_slug:
                logger.info(
                    "PretixSyncItem %s: creating new subevent for entity %s.",
                    self.pk, self.related_entity_id,
                )
                result = client.create_subevent(
                    organizer_slug=target.organizer_slug,
                    event_slug=association.event_slug,
                    payload=push_payload,
                )
                self.subevent_slug = str(result["id"])
                self.save(update_fields=["subevent_slug", "updated_at"])

            # Always patch to ensure item_overrides (prices) are applied.
            # Pretix may ignore item_overrides on creation, so we patch unconditionally.
            client.patch_subevent(
                organizer_slug=target.organizer_slug,
                event_slug=association.event_slug,
                subevent_id=self.subevent_slug,
                payload=push_payload,
            )

            all_item_ids = self._resolve_all_item_ids(association, items)
            if not all_item_ids:
                logger.warning(
                    "PretixSyncItem %s: no Pretix item IDs resolved for event_slug %r. "
                    "Quota will be created without products.",
                    self.pk, association.event_slug,
                )
            self._create_or_update_quota(
                client, target, association, self.subevent_slug,
                title, all_item_ids, max_participants,
            )
            logger.info(
                "PretixSyncItem %s: push completed successfully (subevent %s).",
                self.pk, self.subevent_slug,
            )

        except Exception as exc:
            logger.error(
                "PretixSyncItem %s: push failed for entity %s (subevent_slug=%s): %s",
                self.pk, self.related_entity_id, self.subevent_slug, exc,
                exc_info=True,
            )
            raise RuntimeError(f"sync_pretix push failed: {exc}") from exc

        finally:
            # Always pull after push (success or failure) to keep pretix_data current.
            # A failed pull is logged but must not mask the original push exception.
            try:
                self.pull_update()
            except Exception as pull_exc:
                logger.error(
                    "PretixSyncItem %s: pull_update() after push failed "
                    "(subevent_slug=%s): %s",
                    self.pk, self.subevent_slug, pull_exc,
                    exc_info=True,
                )

    def _create_or_update_quota(
        self,
        client: PretixApiClient,
        target: "PretixSyncTarget",
        association: "PretixSyncTargetAreaAssociation",
        subevent_id: str,
        quota_name: str,
        item_ids: list[int],
        max_participants: int | None,
    ) -> None:
        """Create or update the subevent quota covering all ticket products."""
        quota_payload = {
            "name": quota_name,
            "size": max_participants,
            "items": item_ids,
            "subevent": int(subevent_id),
        }
        existing = client.list_quotas(
            organizer_slug=target.organizer_slug,
            event_slug=association.event_slug,
            subevent_id=subevent_id,
        )
        if existing:
            logger.info(
                "Updating existing quota %s for subevent %s with %d product(s), size=%s.",
                existing[0]["id"], subevent_id, len(item_ids), max_participants,
            )
            client.patch_quota(
                organizer_slug=target.organizer_slug,
                event_slug=association.event_slug,
                quota_id=str(existing[0]["id"]),
                payload=quota_payload,
            )
        else:
            logger.info(
                "Creating quota for subevent %s with %d product(s), size=%s.",
                subevent_id, len(item_ids), max_participants,
            )
            client.create_quota(
                organizer_slug=target.organizer_slug,
                event_slug=association.event_slug,
                payload=quota_payload,
            )

    @staticmethod
    def _resolve_all_item_ids(
        association: "PretixSyncTargetAreaAssociation",
        items: list[dict],
    ) -> list[int]:
        """Return resolved Pretix item IDs for all ticket products in the association."""
        product_names_or_ids = [
            association.ticket_product_member_regular_id,
            association.ticket_product_member_discounted_id,
            association.ticket_product_guest_regular_id,
            association.ticket_product_guest_discounted_id,
            association.ticket_product_business_id,
            association.ticket_product_internal_training_id,
        ]
        return [
            item_id
            for name_or_id in product_names_or_ids
            if (item_id := PretixSyncItem._resolve_item_id(items, name_or_id)) is not None
        ]

    @staticmethod
    def _resolve_item_id(items: list[dict], name_or_id: str | None) -> int | None:
        """Resolve a Pretix item ID from a numeric ID string or a localized display name.

        Name matching is case-insensitive and whitespace-stripped to tolerate
        minor differences between what the management command stored and what
        Pretix returns.
        """
        if not name_or_id:
            return None
        if name_or_id.isdigit():
            return int(name_or_id)
        needle = name_or_id.strip().lower()
        for item in items:
            names = item.get("name") or {}
            if any(v.strip().lower() == needle for v in names.values()):
                return int(item["id"])
        return None

    @staticmethod
    def _build_item_overrides(
        association: "PretixSyncTargetAreaAssociation",
        prices: dict,
        items: list[dict],
    ) -> list[dict]:
        """Map each ticket product in the association to a Pretix price override
        entry, reading prices from the synced_payload["prices"] dict."""
        overrides = []
        for price_key, assoc_attr in PretixSyncItem.PRICE_PROPERTY_MAP:
            name_or_id = getattr(association, assoc_attr)
            price = prices.get(price_key)
            if price is None:
                continue
            item_id = PretixSyncItem._resolve_item_id(items, name_or_id)
            if item_id is not None:
                overrides.append({"item": item_id, "price": str(price)})
        return overrides



class PretixPricingConfiguration(HistoricalMetaBase):
    """
    Global pricing configuration for course fee calculation.

    Based on Kursgebühren-Rechner (documentation/kursgebuehren_rechner_marimo(1).py).
    Contains all configurable parameters that are NOT course-specific.
    """

    # Preparation and lecturer rates
    prep_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.0,
        validators=[MinValueValidator(0)],
        verbose_name="Vorbereitungszeit (Stunden)",
        help_text="Standard-Vorbereitungszeit in Stunden",
    )

    lecturer_rate = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=40.0,
        validators=[MinValueValidator(0)],
        verbose_name="Dozent:in Honorar pro Stunde (€)",
        help_text="Honorar für Dozent:innen pro Stunde",
    )

    # Workshop rates (different for basis courses vs regular courses)
    workshop_rate_basis = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=10.0,
        validators=[MinValueValidator(0)],
        verbose_name="Werkstatt & ZAM Satz Grundkurs (€/h)",
        help_text="Stundensatz für Werkstatt & ZAM bei Grundkursen",
    )

    workshop_rate_regular = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=20.0,
        validators=[MinValueValidator(0)],
        verbose_name="Werkstatt & ZAM Satz Regelfall (€/h)",
        help_text="Stundensatz für Werkstatt & ZAM bei regulären Kursen",
    )

    # Surcharges and discounts
    guest_surcharge = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=10.0,
        validators=[MinValueValidator(0)],
        verbose_name="Gäst:in-Aufschlag (€/h)",
        help_text="Aufschlag für Gäste pro Stunde",
    )

    discount_rate = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=0.50,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        verbose_name="Ermäßigungssatz",
        help_text="Ermäßigungssatz als Dezimalzahl (z.B. 0.50 für 50%)",
    )

    business_surcharge = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=0.75,
        validators=[MinValueValidator(0)],
        verbose_name="Gewerbe-Aufschlag",
        help_text="Aufschlag für gewerbliche Teilnehmer als Dezimalzahl (z.B. 0.75 für 75%)",
    )

    # Tax rate
    vat_rate = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=0.07,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        verbose_name="Umsatzsteuersatz",
        help_text="Umsatzsteuersatz als Dezimalzahl (z.B. 0.07 für 7%)",
    )

    # Min participants calculation parameters
    min_participants_params = models.JSONField(
        default=default_min_participants_params,
        verbose_name="Min. Teilnehmerzahl Parameter",
        help_text="Parameter für Berechnung der Mindestteilnehmerzahl im Format {threshold: deduction}. "
        "Beispiel: {0: 1, 7: 2} bedeutet: Abzug von 1 für 1-6 Teilnehmer, Abzug von 2 für 7+ Teilnehmer. "
        "Formel: max_participants - deduction (wobei threshold <= max_participants)",
    )

    class Meta:
        verbose_name = "Preiskonfiguration"
        verbose_name_plural = "Preiskonfiguration"

    def __str__(self):
        return "Pricing Configuration"

    @staticmethod
    def _to_decimal(value: int | float | Decimal | str) -> Decimal:
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @staticmethod
    def _roundup_euro(value: Decimal) -> Decimal:
        return value.to_integral_value(rounding=ROUND_CEILING).quantize(Decimal("0.01"))

    @property
    def min_participants_thresholds(self) -> list[tuple[int, int]]:
        """Readonly normalized threshold mapping sorted ascending by threshold."""
        raw = self.min_participants_params or {}
        normalized: list[tuple[int, int]] = []
        for threshold, deduction in raw.items():
            normalized.append((int(threshold), int(deduction)))
        normalized.sort(key=lambda item: item[0])
        return normalized

    def get_min_participants(self, max_participants: int) -> int:
        """Apply documented threshold logic: max_participants - deduction."""
        mp = int(max_participants)
        deduction = 0
        for threshold, configured_deduction in self.min_participants_thresholds:
            if threshold <= mp:
                deduction = configured_deduction
            else:
                break
        return max(mp - deduction, 1)

    def get_workshop_rate(self, is_basic_course: bool) -> Decimal:
        return (
            self._to_decimal(self.workshop_rate_basis)
            if is_basic_course
            else self._to_decimal(self.workshop_rate_regular)
        )

    def get_member_regular_price(
        self,
        *,
        duration_hours: int | float | Decimal,
        material_cost: int | float | Decimal,
        max_participants: int,
        is_basic_course: bool,
    ) -> Decimal:
        duration = self._to_decimal(duration_hours)
        material = self._to_decimal(material_cost)
        workshop_rate = self.get_workshop_rate(is_basic_course)
        lecturer_rate = self._to_decimal(self.lecturer_rate)
        prep_hours = self._to_decimal(self.prep_hours)
        vat_rate = self._to_decimal(self.vat_rate)
        min_participants = self.get_min_participants(max_participants)

        value = (
            duration * (workshop_rate + lecturer_rate) + lecturer_rate * prep_hours
        ) * (Decimal("1") + vat_rate) / Decimal(min_participants) + material
        return self._roundup_euro(value)

    def get_member_discounted_price(
        self,
        *,
        duration_hours: int | float | Decimal,
        material_cost: int | float | Decimal,
        max_participants: int,
        is_basic_course: bool,
    ) -> Decimal:
        duration = self._to_decimal(duration_hours)
        material = self._to_decimal(material_cost)
        workshop_rate = self.get_workshop_rate(is_basic_course)
        lecturer_rate = self._to_decimal(self.lecturer_rate)
        prep_hours = self._to_decimal(self.prep_hours)
        vat_rate = self._to_decimal(self.vat_rate)
        discount_rate = self._to_decimal(self.discount_rate)
        min_participants = self.get_min_participants(max_participants)

        value = (
            duration * (workshop_rate * (Decimal("1") - discount_rate) + lecturer_rate)
            + lecturer_rate * prep_hours
        ) * (Decimal("1") + vat_rate) / Decimal(min_participants) + material
        return self._roundup_euro(value)

    def get_guest_regular_price(
        self,
        *,
        duration_hours: int | float | Decimal,
        material_cost: int | float | Decimal,
        max_participants: int,
        is_basic_course: bool,
    ) -> Decimal:
        duration = self._to_decimal(duration_hours)
        material = self._to_decimal(material_cost)
        workshop_rate = self.get_workshop_rate(is_basic_course)
        lecturer_rate = self._to_decimal(self.lecturer_rate)
        prep_hours = self._to_decimal(self.prep_hours)
        guest_surcharge = self._to_decimal(self.guest_surcharge)
        vat_rate = self._to_decimal(self.vat_rate)
        min_participants = self.get_min_participants(max_participants)

        value = (
            duration * (workshop_rate + guest_surcharge + lecturer_rate)
            + lecturer_rate * prep_hours
        ) * (Decimal("1") + vat_rate) / Decimal(min_participants) + material
        return self._roundup_euro(value)

    def get_guest_discounted_price(
        self,
        *,
        duration_hours: int | float | Decimal,
        material_cost: int | float | Decimal,
        max_participants: int,
        is_basic_course: bool,
    ) -> Decimal:
        # Matches the documentation sheet behavior exactly.
        return self.get_member_regular_price(
            duration_hours=duration_hours,
            material_cost=material_cost,
            max_participants=max_participants,
            is_basic_course=is_basic_course,
        )

    def get_business_net_price(
        self,
        *,
        duration_hours: int | float | Decimal,
        material_cost: int | float | Decimal,
        max_participants: int,
        is_basic_course: bool,
    ) -> Decimal:
        duration = self._to_decimal(duration_hours)
        material = self._to_decimal(material_cost)
        workshop_rate = self.get_workshop_rate(is_basic_course)
        lecturer_rate = self._to_decimal(self.lecturer_rate)
        prep_hours = self._to_decimal(self.prep_hours)
        guest_surcharge = self._to_decimal(self.guest_surcharge)
        business_surcharge = self._to_decimal(self.business_surcharge)
        min_participants = self.get_min_participants(max_participants)

        base = self._roundup_euro(
            (
                (
                    duration * (workshop_rate + guest_surcharge + lecturer_rate)
                    + lecturer_rate * prep_hours
                )
                / Decimal(min_participants)
                + material
            )
        )
        price = (Decimal(base) * (Decimal("1") + business_surcharge)).quantize(
            Decimal("0.01")
        )
        return self._roundup_euro(price)

    def get_internal_training_price(
        self,
        *,
        material_cost: int | float | Decimal,
    ) -> Decimal:
        return self._to_decimal(material_cost)

    def get_calculated_prices(
        self,
        *,
        duration_hours: int | float | Decimal,
        material_cost: int | float | Decimal,
        max_participants: int,
        is_basic_course: bool,
    ) -> "CalculatedPriceValues":
        member_regular = self.get_member_regular_price(
            duration_hours=duration_hours,
            material_cost=material_cost,
            max_participants=max_participants,
            is_basic_course=is_basic_course,
        )
        return CalculatedPriceValues(
            member_regular_gross_eur=member_regular,
            member_discounted_gross_eur=self.get_member_discounted_price(
                duration_hours=duration_hours,
                material_cost=material_cost,
                max_participants=max_participants,
                is_basic_course=is_basic_course,
            ),
            guest_regular_gross_eur=self.get_guest_regular_price(
                duration_hours=duration_hours,
                material_cost=material_cost,
                max_participants=max_participants,
                is_basic_course=is_basic_course,
            ),
            guest_discounted_gross_eur=self.get_guest_discounted_price(
                duration_hours=duration_hours,
                material_cost=material_cost,
                max_participants=max_participants,
                is_basic_course=is_basic_course,
            ),
            business_net_eur=self.get_business_net_price(
                duration_hours=duration_hours,
                material_cost=material_cost,
                max_participants=max_participants,
                is_basic_course=is_basic_course,
            ),
            internal_training_eur=self.get_internal_training_price(
                material_cost=material_cost,
            ),
        )


@dataclass(frozen=True)
class CalculatedPriceValues:
    """Readonly calculated prices derived from one course configuration."""

    member_regular_gross_eur: Decimal
    member_discounted_gross_eur: Decimal
    guest_regular_gross_eur: Decimal
    guest_discounted_gross_eur: Decimal
    business_net_eur: Decimal
    internal_training_eur: Decimal


class CalculatedPrices(HistoricalMetaBase):
    """Persisted event prices. Empty fields are auto-filled from proposal data."""

    event = models.OneToOneField(
        "apiv1.Event",
        on_delete=models.CASCADE,
        related_name="calculated_prices",
    )
    pricing_configuration = models.ForeignKey(
        PretixPricingConfiguration,
        on_delete=models.SET_NULL,
        related_name="calculated_prices",
        null=True,
        blank=True,
        default=None,
    )
    member_regular_gross_eur = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )
    member_discounted_gross_eur = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )
    guest_regular_gross_eur = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )
    guest_discounted_gross_eur = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )
    business_net_eur = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )
    internal_training_eur = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        verbose_name="Interne Fortbildung (EUR)",
    )

    class Meta:
        verbose_name = "Berechnete Eventpreise"
        verbose_name_plural = "Berechnete Eventpreise"

    def __str__(self):
        return f"Calculated prices for event {self.event_id}"

    @property
    def proposal(self):
        if not self.event_id:
            return None
        return self.event.proposal

    @property
    def duration_hours(self) -> Decimal:
        proposal = self.proposal
        if proposal is None:
            return Decimal("0")
        total_minutes = time_string_to_minutes(proposal.duration_time_per_day) * int(
            proposal.duration_days
        )
        return Decimal(total_minutes) / Decimal("60")

    @property
    def max_participants(self) -> int:
        proposal = self.proposal
        return int(proposal.max_participants) if proposal is not None else 0

    @property
    def material_cost(self) -> Decimal:
        proposal = self.proposal
        return (
            Decimal(str(proposal.material_cost_eur))
            if proposal is not None
            else Decimal("0")
        )

    @property
    def is_basic_course(self) -> bool:
        proposal = self.proposal
        return bool(proposal.is_basic_course) if proposal is not None else False

    @staticmethod
    def _get_default_pricing_configuration() -> PretixPricingConfiguration:
        latest = PretixPricingConfiguration.objects.order_by("-created_at").first()
        if latest is not None:
            return latest
        return PretixPricingConfiguration.objects.create()

    def clean(self):
        super().clean()

        if not self.event_id:
            return
        if self.proposal is None:
            raise ValidationError(
                {"event": "Linked event must have a proposal to calculate prices."}
            )
        if getattr(self, "_skip_price_generation", False):
            return
        if self.pricing_configuration_id is None:
            self.pricing_configuration = self._get_default_pricing_configuration()

        calculated = self.pricing_configuration.get_calculated_prices(
            duration_hours=self.duration_hours,
            material_cost=self.material_cost,
            max_participants=self.max_participants,
            is_basic_course=self.is_basic_course,
        )

        if self.member_regular_gross_eur is None:
            self.member_regular_gross_eur = calculated.member_regular_gross_eur
        if self.member_discounted_gross_eur is None:
            self.member_discounted_gross_eur = calculated.member_discounted_gross_eur
        if self.guest_regular_gross_eur is None:
            self.guest_regular_gross_eur = calculated.guest_regular_gross_eur
        if self.guest_discounted_gross_eur is None:
            self.guest_discounted_gross_eur = calculated.guest_discounted_gross_eur
        if self.business_net_eur is None:
            self.business_net_eur = calculated.business_net_eur
        if self.internal_training_eur is None:
            self.internal_training_eur = calculated.internal_training_eur

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
