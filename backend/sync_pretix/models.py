from django.db import models
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from dataclasses import dataclass
from datetime import datetime, timezone as _tz
from decimal import Decimal, ROUND_CEILING
import logging
import re

from apiv1.models import SyncBaseTarget, ProposalArea
from apiv1.models.basedata import time_string_to_minutes
from apiv1.models.sync.syncbasedata import SyncBaseItem, SyncDiffData, PropertyDiff
from project.basemodels import HistoricalMetaBase
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
    secret_field_names = ["api_token"]

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

    def create_new_sync_item(self, event) -> "PretixSyncItem":
        """Create a PretixSyncItem for *event* using the area-to-event-slug mapping.

        Looks up the event's proposal area and finds the matching
        ``PretixSyncTargetAreaAssociation`` to determine the correct Pretix
        event slug.  The call is idempotent: if an item already exists for this
        (target, event) pair the existing item is returned unchanged.

        Raises ``ValueError`` if the event has no proposal, the proposal has no
        area, or no association is configured for that area on this target.
        """
        proposal = getattr(event, "proposal", None)
        if proposal is None:
            raise ValueError(
                f"Event {event.pk} has no proposal; cannot determine Pretix event slug."
            )
        area = getattr(proposal, "area", None)
        if area is None:
            raise ValueError(
                f"Proposal {proposal.pk} has no area; cannot determine Pretix event slug."
            )
        try:
            association = self.area_associations.get(area=area)
        except PretixSyncTargetAreaAssociation.DoesNotExist:
            raise ValueError(
                f"No Pretix event-slug configured for area {area.code!r} "
                f"on sync target {self.pk}."
            )
        item, _created = PretixSyncItem.objects.get_or_create(
            sync_target=self,
            related_event=event,
            defaults={"area_association": association, "flag_push": True},
        )
        return item


class PretixSyncTargetAreaAssociation(HistoricalMetaBase):
    sync_target = models.ForeignKey(
        PretixSyncTarget,
        on_delete=models.CASCADE,
        related_name="area_associations",
        null=True,
        blank=True,
    )
    area = models.ForeignKey(
        ProposalArea, on_delete=models.CASCADE, related_name="pretix_sync_associations"
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
    """Links a Pretix subevent to an internal event via a PretixSyncTarget."""

    sync_target = models.ForeignKey(
        PretixSyncTarget,
        on_delete=models.CASCADE,
        related_name="items",
    )
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
        help_text="ID of the Pretix subevent created for this event. Set on first push.",
    )
    pretix_data = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Pretix Subevent Data",
        help_text=(
            "Latest subevent and quota data fetched from Pretix after the most recent push. "
            "Structure: {\"subevent\": {...}, \"quotas\": [...]}"
        ),
    )

    def __str__(self):
        return f"PretixSyncItem(target={self.sync_target_id}, event={self.related_event_id})"

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

    def get_status(self) -> "SyncBaseTarget.SyncTargetStatus":
        """Return the sync status for this Pretix sync item.

        - ``subevent_slug`` is None        → CREATION_PENDING  (sync item exists but
                                                                   not yet pushed to Pretix)
        - ``subevent_slug`` set, no data   → STATUS_UNKNOWN    (pushed but pull not done
                                                                   yet or pull failed)
        - data present, no differences     → ENTRY_UP_TO_DATE
        - data present, differences found  → ENTRY_DIFFERS
        """
        if not self.subevent_slug:
            logger.debug(
                "PretixSyncItem %s: status=CREATION_PENDING (subevent not yet created).", self.pk
            )
            return SyncBaseTarget.SyncTargetStatus.CREATION_PENDING
        if self.pretix_data is None:
            logger.debug(
                "PretixSyncItem %s: status=STATUS_UNKNOWN "
                "(subevent_slug=%s but pretix_data not pulled yet).",
                self.pk, self.subevent_slug,
            )
            return SyncBaseTarget.SyncTargetStatus.STATUS_UNKNOWN
        diff = self.sync_diff()
        if diff is None:
            # sync_diff() only returns None when subevent_slug is absent, which we
            # already handled above – treat as unknown defensively.
            logger.warning(
                "PretixSyncItem %s: sync_diff() returned None unexpectedly "
                "(subevent_slug=%s, pretix_data present). Reporting STATUS_UNKNOWN.",
                self.pk, self.subevent_slug,
            )
            return SyncBaseTarget.SyncTargetStatus.STATUS_UNKNOWN
        if diff.properties:
            logger.debug(
                "PretixSyncItem %s: status=ENTRY_DIFFERS (%d property diff(s): %s).",
                self.pk,
                len(diff.properties),
                [p.property_name for p in diff.properties],
            )
            return SyncBaseTarget.SyncTargetStatus.ENTRY_DIFFERS
        logger.debug("PretixSyncItem %s: status=ENTRY_UP_TO_DATE.", self.pk)
        return SyncBaseTarget.SyncTargetStatus.ENTRY_UP_TO_DATE

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
            "PretixSyncItem %s: deleted Pretix subevent %s for event %s.",
            self.pk, self.subevent_slug, self.related_event_id,
        )
        self.subevent_slug = None
        self.pretix_data = None
        self.save(update_fields=["subevent_slug", "pretix_data", "updated_at"])

    def pull_update(self) -> None:
        """Fetch the current subevent and its quotas from Pretix and store in pretix_data."""
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
        logger.debug(
            "PretixSyncItem %s: subevent fetched (name=%r, date_from=%r, date_to=%r).",
            self.pk,
            (subevent.get("name") or {}).get("de", "?"),
            subevent.get("date_from"),
            subevent.get("date_to"),
        )
        quotas = client.list_quotas(
            organizer_slug=target.organizer_slug,
            event_slug=association.event_slug,
            subevent_id=self.subevent_slug,
        )
        logger.debug(
            "PretixSyncItem %s: fetched %d quota(s) for subevent %s.",
            self.pk, len(quotas), self.subevent_slug,
        )
        items = client.list_items(
            organizer_slug=target.organizer_slug,
            event_slug=association.event_slug,
        )
        logger.debug(
            "PretixSyncItem %s: fetched %d item(s) for event %r.",
            self.pk, len(items), association.event_slug,
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

    def sync_diff(self, only_differences: bool = True) -> SyncDiffData | None:
        """Compare the calculated configuration against the stored pretix_data.

        Args:
            only_differences: When True (default), only include properties that differ
                between local and remote values. When False, include all comparable
                properties regardless of whether they match, so callers can display
                the full context (e.g. the diff viewer for an up-to-date item).

        Returns:
            SyncDiffData with ``remote_value=""`` on every property when the item has
            never been pushed (CREATION_PENDING preview of what would be created).
            None when the subevent was pushed but pretix_data has not been pulled yet
            (STATUS_UNKNOWN – caller should not interpret this as "up-to-date").
            SyncDiffData with empty ``properties`` (only_differences=True) or all
            properties with equal values (only_differences=False) when remote matches
            local config.
            SyncDiffData with non-empty ``properties`` when differences are detected.
        """
        if not self.subevent_slug:
            # CREATION_PENDING: return a preview diff showing what would be created.
            logger.debug(
                "PretixSyncItem %s: sync_diff() returning creation preview "
                "(subevent not yet pushed).",
                self.pk,
            )
            return self._build_creation_preview_diff()

        if self.pretix_data is None:
            # STATUS_UNKNOWN: pushed but pull hasn't happened / failed.
            return None

        event = self.related_event
        association = self.area_association
        if association is None:
            return None

        target = self.sync_target

        # Determine the event locale used for name comparison.
        event_locale = "de"
        proposal = getattr(event, "proposal", None)
        if proposal is not None:
            lang = getattr(proposal, "language", None)
            if lang is not None:
                event_locale = lang.code

        stored_subevent = self.pretix_data.get("subevent") or {}
        stored_quotas = self.pretix_data.get("quotas") or []

        properties: list[PropertyDiff] = []

        # Compare date_from.
        expected_date_from = event.start_time.isoformat()
        actual_date_from = stored_subevent.get("date_from", "")
        if not only_differences or not _isoformat_equal(expected_date_from, actual_date_from):
            properties.append(PropertyDiff(
                property_name="date_from",
                local_value=expected_date_from,
                remote_value=actual_date_from,
                file_type="text",
            ))

        # Compare date_to.
        expected_date_to = event.end_time.isoformat()
        actual_date_to = stored_subevent.get("date_to", "")
        if not only_differences or not _isoformat_equal(expected_date_to, actual_date_to):
            properties.append(PropertyDiff(
                property_name="date_to",
                local_value=expected_date_to,
                remote_value=actual_date_to,
                file_type="text",
            ))

        # Compare the event name for the primary locale.
        actual_name_dict = stored_subevent.get("name") or {}
        actual_name = actual_name_dict.get(event_locale, "")
        if not only_differences or event.name != actual_name:
            properties.append(PropertyDiff(
                property_name="name",
                local_value=event.name,
                remote_value=actual_name,
                file_type="text",
            ))

        # Compare quota size (max_participants) against the first matching quota.
        if proposal is not None and stored_quotas:
            expected_size = getattr(proposal, "max_participants", None)
            if expected_size is not None:
                actual_size = stored_quotas[0].get("size")
                if not only_differences or actual_size != int(expected_size):
                    properties.append(PropertyDiff(
                        property_name="quota_size",
                        local_value=str(expected_size),
                        remote_value=str(actual_size),
                        file_type="text",
                    ))

        # Compare calculated prices against stored item_price_overrides.
        stored_items = self.pretix_data.get("items") or []
        properties.extend(
            self._compare_prices(event, association, stored_subevent, stored_items,
                                 only_differences=only_differences)
        )

        return SyncDiffData(
            series_id=event.series_id,
            event_id=event.pk,
            target_id=target.pk,
            properties=properties,
        )

    def push_update(self) -> None:
        """Create or update the linked Pretix subevent, overriding ticket prices.

        A ``pull_update()`` is performed in a ``finally`` block so that
        ``pretix_data`` (and therefore the sync status) is always refreshed,
        even when the push itself raises an error.
        """
        association = self.area_association
        if association is None:
            raise ValueError(
                f"PretixSyncItem {self.pk} has no area association; cannot push."
            )

        target = self.sync_target
        event = self.related_event
        logger.info(
            "PretixSyncItem %s: starting push for event %s (subevent_slug=%s, "
            "organizer=%s, event_slug=%s).",
            self.pk, event.pk, self.subevent_slug,
            target.organizer_slug, association.event_slug,
        )
        client = PretixApiClient(api_base_url=target.api_url, token=target.api_token)

        try:
            # Determine the event locale from the proposal language; fall back to "de".
            event_locale = "de"
            proposal = getattr(event, "proposal", None)
            if proposal is not None:
                lang = getattr(proposal, "language", None)
                if lang is not None:
                    event_locale = lang.code

            # Fetch the Pretix parent event to get its configured locales.
            logger.debug(
                "PretixSyncItem %s: fetching parent event %r from Pretix.",
                self.pk, association.event_slug,
            )
            pretix_event = client.get_event(
                organizer_slug=target.organizer_slug,
                event_slug=association.event_slug,
            )
            configured_locales = pretix_event.get("locales") or [event_locale]
            # Apply the same name to every configured locale; ensure the event's own locale is present.
            name_dict = {locale: event.name for locale in configured_locales}
            name_dict[event_locale] = event.name
            logger.debug(
                "PretixSyncItem %s: parent event locales=%r, name_dict=%r.",
                self.pk, configured_locales, name_dict,
            )

            # Always fetch items — needed for both price overrides and the quota.
            items = client.list_items(
                organizer_slug=target.organizer_slug,
                event_slug=association.event_slug,
            )
            logger.debug(
                "PretixSyncItem %s: fetched %d item(s) from Pretix event %r: %s",
                self.pk, len(items),
                association.event_slug,
                [(i.get("id"), i.get("name")) for i in items],
            )

            # Build price overrides from CalculatedPrices if available.
            item_overrides: list[dict] = []
            try:
                prices = event.calculated_prices
                item_overrides = self._build_item_overrides(association, prices, items)
                logger.debug(
                    "PretixSyncItem %s: built %d item_override(s): %s",
                    self.pk, len(item_overrides), item_overrides,
                )
                if not item_overrides:
                    logger.warning(
                        "PretixSyncItem %s: CalculatedPrices exist for event %s but produced "
                        "no item overrides. Check that prices are non-null and product names "
                        "match Pretix items.",
                        self.pk, event.pk,
                    )
            except ObjectDoesNotExist:
                logger.warning(
                    "PretixSyncItem %s: no CalculatedPrices found for event %s; "
                    "subevent will use default item prices.",
                    self.pk, event.pk,
                )

            payload = {
                "name": name_dict,
                "date_from": event.start_time.isoformat(),
                "date_to": event.end_time.isoformat(),
                "active": True,
                "meta_data": {},
                "item_price_overrides": item_overrides,
            }

            # Create the subevent if this is the first push.
            if not self.subevent_slug:
                logger.info(
                    "PretixSyncItem %s: creating new subevent for event %s.",
                    self.pk, event.pk,
                )
                result = client.create_subevent(
                    organizer_slug=target.organizer_slug,
                    event_slug=association.event_slug,
                    payload=payload,
                )
                self.subevent_slug = str(result["id"])
                self.save(update_fields=["subevent_slug", "updated_at"])
                logger.info(
                    "PretixSyncItem %s: created subevent id=%s.", self.pk, self.subevent_slug,
                )

            # Always patch to ensure item_overrides (prices) are applied.
            # Pretix may ignore item_overrides on creation, so we patch unconditionally.
            logger.debug(
                "PretixSyncItem %s: patching subevent %s with payload (date_from=%r, date_to=%r, "
                "%d overrides).",
                self.pk, self.subevent_slug,
                payload["date_from"], payload["date_to"], len(item_overrides),
            )
            client.patch_subevent(
                organizer_slug=target.organizer_slug,
                event_slug=association.event_slug,
                subevent_id=self.subevent_slug,
                payload=payload,
            )
            logger.debug("PretixSyncItem %s: patch_subevent succeeded.", self.pk)

            # Create or update the quota so price overrides take effect.
            max_participants = getattr(proposal, "max_participants", None)
            resolved_items_and_variations = self._resolve_all_items_and_variations(
                association, items
            )
            all_item_ids: list[int] = []
            for item_id, _variation_id in resolved_items_and_variations:
                if item_id not in all_item_ids:
                    all_item_ids.append(item_id)
            all_variation_ids = [
                variation_id
                for _item_id, variation_id in resolved_items_and_variations
                if variation_id is not None
            ]
            if not all_item_ids:
                logger.warning(
                    "PretixSyncItem %s: no Pretix item IDs resolved for event %r "
                    "(event_slug %r). Expected product names: %s. Fetched items: %s. "
                    "Quota will be created without products.",
                    self.pk,
                    event.name,
                    association.event_slug,
                    [
                        association.ticket_product_member_regular_id,
                        association.ticket_product_member_discounted_id,
                        association.ticket_product_guest_regular_id,
                        association.ticket_product_guest_discounted_id,
                        association.ticket_product_business_id,
                        association.ticket_product_internal_training_id,
                    ],
                    [(i.get("id"), i.get("name")) for i in items],
                )
            else:
                logger.debug(
                    "PretixSyncItem %s: resolved %d item ID(s) for quota: %s",
                    self.pk, len(all_item_ids), all_item_ids,
                )
            self._create_or_update_quota(
                client, target, association, self.subevent_slug,
                event.name, all_item_ids, max_participants,
                variation_ids=all_variation_ids,
            )
            logger.info(
                "PretixSyncItem %s: push completed successfully (subevent %s).",
                self.pk, self.subevent_slug,
            )

        except Exception as exc:
            logger.error(
                "PretixSyncItem %s: push failed for event %s (subevent_slug=%s): %s",
                self.pk, event.pk, self.subevent_slug, exc,
                exc_info=True,
            )
            raise

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

    def _build_creation_preview_diff(self) -> SyncDiffData:
        """Return a diff showing local values vs an empty remote (creation preview).

        Called by ``sync_diff()`` when the subevent has not been pushed yet
        (CREATION_PENDING).  Every property uses ``remote_value=""`` to indicate
        that nothing exists in Pretix yet.
        """
        event = self.related_event
        target = self.sync_target
        proposal = getattr(event, "proposal", None)

        event_locale = "de"
        if proposal is not None:
            lang = getattr(proposal, "language", None)
            if lang is not None:
                event_locale = lang.code

        properties: list[PropertyDiff] = [
            PropertyDiff(
                property_name="name",
                local_value=event.name,
                remote_value="",
                file_type="text",
            ),
            PropertyDiff(
                property_name="date_from",
                local_value=event.start_time.isoformat(),
                remote_value="",
                file_type="text",
            ),
            PropertyDiff(
                property_name="date_to",
                local_value=event.end_time.isoformat(),
                remote_value="",
                file_type="text",
            ),
        ]

        if proposal is not None:
            max_participants = getattr(proposal, "max_participants", None)
            if max_participants is not None:
                properties.append(PropertyDiff(
                    property_name="quota_size",
                    local_value=str(max_participants),
                    remote_value="",
                    file_type="text",
                ))

        # Include calculated prices when available (remote is empty – nothing pushed yet).
        try:
            prices = event.calculated_prices
            price_mapping = [
                (prices.member_regular_gross_eur, "price_member_regular"),
                (prices.member_discounted_gross_eur, "price_member_discounted"),
                (prices.guest_regular_gross_eur, "price_guest_regular"),
                (prices.guest_discounted_gross_eur, "price_guest_discounted"),
                (prices.business_net_eur, "price_business"),
                (prices.internal_training_eur, "price_internal_training"),
            ]
            for price, property_name in price_mapping:
                if price is not None:
                    properties.append(PropertyDiff(
                        property_name=property_name,
                        local_value=str(price),
                        remote_value="",
                        file_type="text",
                    ))
        except ObjectDoesNotExist:
            pass

        logger.debug(
            "PretixSyncItem %s: creation preview diff has %d properties.",
            self.pk, len(properties),
        )
        return SyncDiffData(
            series_id=event.series_id,
            event_id=event.pk,
            target_id=target.pk,
            properties=properties,
        )

    def _compare_prices(
        self,
        event,
        association: "PretixSyncTargetAreaAssociation",
        stored_subevent: dict,
        stored_items: list[dict],
        only_differences: bool = True,
    ) -> list[PropertyDiff]:
        """Compare calculated prices against stored Pretix item_price_overrides.

        Args:
            only_differences: When True (default), only return properties whose
                prices differ. When False, also include prices that match so the
                full context is available for display.

        Returns an empty list when no ``CalculatedPrices`` exist for the event,
        or when ``stored_items`` is empty (item IDs cannot be resolved).
        """
        try:
            prices = event.calculated_prices
        except ObjectDoesNotExist:
            logger.debug(
                "PretixSyncItem %s: no CalculatedPrices for event %s; "
                "skipping price comparison.",
                self.pk, event.pk,
            )
            return []

        actual_overrides: dict[tuple[int, int | None], str] = {
            (override["item"], override.get("variation")): override.get("price", "")
            for override in stored_subevent.get("item_price_overrides") or []
        }
        price_mapping = [
            (association.ticket_product_member_regular_id,
             prices.member_regular_gross_eur, "price_member_regular"),
            (association.ticket_product_member_discounted_id,
             prices.member_discounted_gross_eur, "price_member_discounted"),
            (association.ticket_product_guest_regular_id,
             prices.guest_regular_gross_eur, "price_guest_regular"),
            (association.ticket_product_guest_discounted_id,
             prices.guest_discounted_gross_eur, "price_guest_discounted"),
            (association.ticket_product_business_id,
             prices.business_net_eur, "price_business"),
            (association.ticket_product_internal_training_id,
             prices.internal_training_eur, "price_internal_training"),
        ]

        diffs: list[PropertyDiff] = []
        for name_or_id, expected_price, property_name in price_mapping:
            if expected_price is None:
                continue
            resolved = PretixSyncItem._resolve_item_and_variation(stored_items, name_or_id)
            if resolved is None:
                continue
            item_id, variation_id = resolved
            actual_price_str = actual_overrides.get((item_id, variation_id))
            expected_price_str = str(expected_price)
            try:
                prices_equal = (
                    actual_price_str is not None
                    and Decimal(actual_price_str) == Decimal(expected_price_str)
                )
            except Exception:
                prices_equal = actual_price_str == expected_price_str
            if not only_differences or not prices_equal:
                logger.debug(
                    "PretixSyncItem %s: price diff for %s (item %s): "
                    "local=%s remote=%s.",
                    self.pk, property_name, item_id,
                    expected_price_str, actual_price_str,
                )
                diffs.append(PropertyDiff(
                    property_name=property_name,
                    local_value=expected_price_str,
                    remote_value=actual_price_str or "",
                    file_type="text",
                ))
        return diffs

    def _create_or_update_quota(
        self,
        client: PretixApiClient,
        target: "PretixSyncTarget",
        association: "PretixSyncTargetAreaAssociation",
        subevent_id: str,
        quota_name: str,
        item_ids: list[int],
        max_participants: int | None,
        variation_ids: list[int] | None = None,
    ) -> None:
        """Create or update the subevent quota covering all five ticket products.

        ``item_ids`` covers items sold directly; ``variation_ids`` additionally
        restricts coverage to specific variations for items that have them
        (Pretix requires the parent item ID in ``items`` *and* the variation ID
        in ``variations`` for variation-based quota coverage).
        """
        quota_payload = {
            "name": quota_name,
            "size": max_participants,
            "items": item_ids,
            "variations": variation_ids or [],
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
        """Return resolved Pretix item IDs for all five ticket products in the association.

        For products resolved to a variation, the *parent* item ID is returned
        (deduplicated); use ``_resolve_all_items_and_variations`` to also get
        the variation IDs needed to scope a quota to specific variations.
        """
        resolved = PretixSyncItem._resolve_all_items_and_variations(association, items)
        seen: list[int] = []
        for item_id, _variation_id in resolved:
            if item_id not in seen:
                seen.append(item_id)
        return seen

    @staticmethod
    def _resolve_all_items_and_variations(
        association: "PretixSyncTargetAreaAssociation",
        items: list[dict],
    ) -> list[tuple[int, int | None]]:
        """Return resolved (item_id, variation_id) pairs for all ticket products."""
        product_names_or_ids = [
            association.ticket_product_member_regular_id,
            association.ticket_product_member_discounted_id,
            association.ticket_product_guest_regular_id,
            association.ticket_product_guest_discounted_id,
            association.ticket_product_business_id,
            association.ticket_product_internal_training_id,
        ]
        resolved = []
        for name_or_id in product_names_or_ids:
            pair = PretixSyncItem._resolve_item_and_variation(items, name_or_id)
            if pair is not None:
                resolved.append(pair)
        return resolved

    @staticmethod
    def _resolve_item_and_variation(
        items: list[dict], name_or_id: str | None
    ) -> tuple[int, int | None] | None:
        """Resolve a Pretix (item ID, variation ID) pair for a configured product.

        ``name_or_id`` may be:
        - a plain numeric item ID (``"123"``) — resolves to that item, no variation;
        - an explicit ``"<item_id>/<variation_id>"`` pair;
        - a localized display name of an item (matched case-insensitively,
          whitespace-stripped) — resolves to that item, no variation;
        - a localized display value of one of an item's *variations* (e.g. the
          product is set up as a variation rather than a top-level item) —
          resolves to the parent item's ID plus that variation's ID.

        Items are expected to carry their variations nested under a
        ``"variations"`` key, as returned by the Pretix item list/detail API.
        """
        if not name_or_id:
            return None
        name_or_id = name_or_id.strip()
        if "/" in name_or_id:
            item_part, _, variation_part = name_or_id.partition("/")
            if item_part.isdigit() and variation_part.isdigit():
                return int(item_part), int(variation_part)
        if name_or_id.isdigit():
            return int(name_or_id), None
        needle = name_or_id.lower()
        for item in items:
            names = item.get("name") or {}
            if any(v.strip().lower() == needle for v in names.values()):
                return int(item["id"]), None
        for item in items:
            for variation in item.get("variations") or []:
                values = variation.get("value") or {}
                if any((v or "").strip().lower() == needle for v in values.values()):
                    return int(item["id"]), int(variation["id"])
        return None

    @staticmethod
    def _build_item_overrides(
        association: "PretixSyncTargetAreaAssociation",
        prices: "CalculatedPrices",
        items: list[dict],
    ) -> list[dict]:
        """Map each ticket product in the association to a Pretix price override entry."""
        price_mapping = [
            (association.ticket_product_member_regular_id, prices.member_regular_gross_eur),
            (association.ticket_product_member_discounted_id, prices.member_discounted_gross_eur),
            (association.ticket_product_guest_regular_id, prices.guest_regular_gross_eur),
            (association.ticket_product_guest_discounted_id, prices.guest_discounted_gross_eur),
            (association.ticket_product_business_id, prices.business_net_eur),
            (association.ticket_product_internal_training_id, prices.internal_training_eur),
        ]
        overrides = []
        for name_or_id, price in price_mapping:
            resolved = PretixSyncItem._resolve_item_and_variation(items, name_or_id)
            if resolved is not None and price is not None:
                item_id, variation_id = resolved
                override = {"item": item_id, "price": str(price)}
                if variation_id is not None:
                    override["variation"] = variation_id
                overrides.append(override)
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
