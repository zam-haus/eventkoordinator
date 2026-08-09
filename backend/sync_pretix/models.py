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

    @classmethod
    def sync_item_model(cls):
        return PretixSyncItem


class PretixSyncItem(SyncBaseItem):
    """Links a Pretix subevent to a UDM entity via a PretixSyncTarget.

    Pushes from `self.synced_payload` (the effective-values snapshot taken by
    mark_sync, events-and-sync.md §4.2), not from a live model relation, per
    the `sync_pretix` type-editor tab's binding config (§14): `parent_event`
    resolves the Pretix event slug (pinned into `remote_identity` at first
    push), `bindings` fills subevent fields (title/start/end/locale/
    max_participants), and `items` lists ticket products/variations to push
    price overrides and quota membership for. There is no other
    configuration surface — no per-target admin screen assigns entities to
    events or ticket products.
    """

    BASE_STATUSES: ClassVar[frozenset[str]] = SyncBaseItem.BASE_STATUSES | {"cancelled"}

    # sync_target is inherited as-is from SyncBaseItem (a plain FK to the
    # polymorphic SyncBaseTarget base) — same pattern as sync_caldav/sync_webhook.
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
    remote_identity = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Remote identity",
        help_text=(
            "events-and-sync.md §14: {\"organizer_slug\", \"event_slug\", \"subevent_id\"} "
            "pinned at first successful push via the parent_event binding. Every later "
            "push/pull/delete uses this, never re-resolving parent_event — so a later "
            "change to what parent_event resolves to does not move an already-created "
            "subevent (see compute_drift for the surfaced mismatch). Null until the "
            "first successful push."
        ),
    )

    def __str__(self):
        return f"PretixSyncItem(target={self.sync_target_id}, entity={self.related_entity_id})"

    @classmethod
    def allowed_statuses(cls) -> frozenset[str]:
        return cls.BASE_STATUSES

    @property
    def _resolved_organizer_slug(self) -> str | None:
        if self.remote_identity:
            return self.remote_identity.get("organizer_slug")
        target = self.sync_target
        return target.organizer_slug if target else None

    @property
    def _resolved_event_slug(self) -> str | None:
        return self.remote_identity.get("event_slug") if self.remote_identity else None

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
        target = self.sync_target
        organizer_slug = self._resolved_organizer_slug
        event_slug = self._resolved_event_slug
        if target is None or organizer_slug is None or event_slug is None:
            return None
        server_url = re.sub(r"/api/v1/?$", "", target.api_url.rstrip("/"))
        return (
            f"{server_url}/control/event/{organizer_slug}"
            f"/{event_slug}/subevents/{self.subevent_slug}/"
        )

    def delete_remote(self) -> None:
        """Delete the linked Pretix subevent and reset the stored subevent ID."""
        if not self.subevent_slug:
            logger.info(
                "PretixSyncItem %s: delete_remote() skipped (no subevent_slug).", self.pk
            )
            return  # Nothing to delete remotely.

        organizer_slug = self._resolved_organizer_slug
        event_slug = self._resolved_event_slug
        if organizer_slug is None or event_slug is None:
            raise ValueError(
                f"PretixSyncItem {self.pk} has no resolved event; cannot delete remote subevent."
            )

        target = self.sync_target
        client = PretixApiClient(api_base_url=target.api_url, token=target.api_token)
        logger.info(
            "PretixSyncItem %s: deleting remote subevent %s (event %s/%s).",
            self.pk, self.subevent_slug, event_slug, organizer_slug,
        )
        client.delete_subevent(
            organizer_slug=organizer_slug,
            event_slug=event_slug,
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

        organizer_slug = self._resolved_organizer_slug
        event_slug = self._resolved_event_slug
        if organizer_slug is None or event_slug is None:
            raise ValueError(
                f"PretixSyncItem {self.pk} has no resolved event; cannot pull."
            )

        target = self.sync_target
        client = PretixApiClient(api_base_url=target.api_url, token=target.api_token)

        logger.info(
            "PretixSyncItem %s: pulling subevent %s from %s/%s.",
            self.pk, self.subevent_slug, organizer_slug, event_slug,
        )
        subevent = client.get_subevent(
            organizer_slug=organizer_slug,
            event_slug=event_slug,
            subevent_id=self.subevent_slug,
        )
        quotas = client.list_quotas(
            organizer_slug=organizer_slug,
            event_slug=event_slug,
            subevent_id=self.subevent_slug,
        )
        items = client.list_items(
            organizer_slug=organizer_slug,
            event_slug=event_slug,
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

        # events-and-sync.md §14: parent_event is pinned into remote_identity
        # at first push and never re-resolved by push() itself — surface a
        # freshly-resolved mismatch here instead, so an admin sees "this type
        # now resolves to a different event" without the subevent silently
        # moving underneath them.
        resolved_parent_event = payload.get("parent_event")
        if resolved_parent_event and self.remote_identity:
            pinned_event_slug = self.remote_identity.get("event_slug")
            if pinned_event_slug is not None and resolved_parent_event != pinned_event_slug:
                properties.append(PropertyDiff(
                    property_name="parent_event", old_value=pinned_event_slug, new_value=resolved_parent_event,
                ))

        return SyncDiffData(
            entity_id=str(self.related_entity_id),
            target_key=self.sync_target.key if self.sync_target_id else None,
            properties=properties,
        )

    def push(self) -> None:
        """Create or update the linked Pretix subevent from synced_payload,
        per the `sync_pretix` type-editor tab's binding config (§14):
        `parent_event` resolves the event slug (pinned into
        `remote_identity` at first push — see `_push_via_bindings`), item/
        variation price overrides and quota membership come from
        `payload["items"]`.

        `parent_event` is required for syncing to actually happen, but
        never required to *save* the tab config (events-and-sync.md's "save
        must always be possible" — the frontend has no validation blocking
        an empty value either). A blank/unresolved `parent_event` — no
        policy/field/template configured yet, or one that resolves to
        nothing for this entity — is not an error: push() simply has
        nothing to do yet and returns without touching Pretix or the item's
        status."""
        payload = self.synced_payload or {}
        if not payload.get("parent_event") and self.remote_identity is None:
            logger.info(
                "PretixSyncItem %s: push skipped — parent_event binding is empty/unresolved "
                "and no subevent has been created yet.",
                self.pk,
            )
            return
        self._push_via_bindings(payload)

    def _push_via_bindings(self, payload: dict) -> None:
        """events-and-sync.md §14: dynamic parent-event resolution + item/
        variation bindings. `payload["parent_event"]` is only consulted to
        create the subevent the first time — once `remote_identity` is
        pinned, every later push reuses the pinned event_slug regardless of
        what parent_event now resolves to (see compute_drift)."""
        target = self.sync_target
        title = payload.get("title", "")
        start = payload.get("start")
        end = payload.get("end")
        locale = payload.get("locale") or "de"
        max_participants = payload.get("max_participants")
        items_config = payload.get("items") or []

        if self.remote_identity:
            organizer_slug = self.remote_identity["organizer_slug"]
            event_slug = self.remote_identity["event_slug"]
        else:
            organizer_slug = target.organizer_slug
            event_slug = payload["parent_event"]

        logger.info(
            "PretixSyncItem %s: starting bindings push for entity %s (subevent_slug=%s, "
            "organizer=%s, event_slug=%s).",
            self.pk, self.related_entity_id, self.subevent_slug, organizer_slug, event_slug,
        )
        client = PretixApiClient(api_base_url=target.api_url, token=target.api_token)

        try:
            pretix_event = client.get_event(organizer_slug=organizer_slug, event_slug=event_slug)
            configured_locales = pretix_event.get("locales") or [locale]
            name_dict = {loc: title for loc in configured_locales}
            name_dict[locale] = title

            items = client.list_items(organizer_slug=organizer_slug, event_slug=event_slug)

            item_overrides, variation_overrides = self._build_binding_price_overrides(items_config, items)

            push_payload = {
                "name": name_dict,
                "date_from": start,
                "date_to": end,
                "active": True,
                "meta_data": {},
                "item_price_overrides": item_overrides,
                "variation_price_overrides": variation_overrides,
            }

            if not self.subevent_slug:
                logger.info(
                    "PretixSyncItem %s: creating new subevent for entity %s (event %s/%s).",
                    self.pk, self.related_entity_id, organizer_slug, event_slug,
                )
                result = client.create_subevent(
                    organizer_slug=organizer_slug, event_slug=event_slug, payload=push_payload,
                )
                self.subevent_slug = str(result["id"])
                self.remote_identity = {
                    "organizer_slug": organizer_slug,
                    "event_slug": event_slug,
                    "subevent_id": self.subevent_slug,
                }
                self.save(update_fields=["subevent_slug", "remote_identity", "updated_at"])

            # Always patch to ensure item/variation overrides (prices) are applied.
            # Pretix may ignore overrides on creation, so we patch unconditionally.
            client.patch_subevent(
                organizer_slug=organizer_slug, event_slug=event_slug,
                subevent_id=self.subevent_slug, payload=push_payload,
            )

            quota_item_ids, quota_variation_ids = self._resolve_binding_quota_members(items_config, items)
            if items_config and not quota_item_ids and not quota_variation_ids:
                logger.warning(
                    "PretixSyncItem %s: item bindings configured but produced no quota "
                    "members. Check that item/variation names/ids match Pretix.",
                    self.pk,
                )
            self._create_or_update_quota(
                client, organizer_slug, event_slug, self.subevent_slug,
                title, quota_item_ids, quota_variation_ids, max_participants,
            )
            logger.info(
                "PretixSyncItem %s: bindings push completed successfully (subevent %s).",
                self.pk, self.subevent_slug,
            )

        except Exception as exc:
            logger.error(
                "PretixSyncItem %s: bindings push failed for entity %s (subevent_slug=%s): %s",
                self.pk, self.related_entity_id, self.subevent_slug, exc,
                exc_info=True,
            )
            raise RuntimeError(f"sync_pretix push failed: {exc}") from exc

        finally:
            try:
                self.pull_update()
            except Exception as pull_exc:
                logger.error(
                    "PretixSyncItem %s: pull_update() after push failed "
                    "(subevent_slug=%s): %s",
                    self.pk, self.subevent_slug, pull_exc,
                    exc_info=True,
                )

    @staticmethod
    def _create_or_update_quota(
        client: PretixApiClient,
        organizer_slug: str,
        event_slug: str,
        subevent_id: str,
        quota_name: str,
        item_ids: list[int],
        variation_ids: list[int],
        max_participants: int | None,
    ) -> None:
        """Create or update the subevent quota covering all bound ticket
        products/variations. Shared by both push paths (§14) — the legacy
        path always passes an empty `variation_ids` since it has no
        variation concept."""
        quota_payload = {
            "name": quota_name,
            "size": max_participants,
            "items": item_ids,
            "variations": variation_ids,
            "subevent": int(subevent_id),
        }
        existing = client.list_quotas(
            organizer_slug=organizer_slug,
            event_slug=event_slug,
            subevent_id=subevent_id,
        )
        if existing:
            logger.info(
                "Updating existing quota %s for subevent %s with %d item(s)/%d variation(s), size=%s.",
                existing[0]["id"], subevent_id, len(item_ids), len(variation_ids), max_participants,
            )
            client.patch_quota(
                organizer_slug=organizer_slug,
                event_slug=event_slug,
                quota_id=str(existing[0]["id"]),
                payload=quota_payload,
            )
        else:
            logger.info(
                "Creating quota for subevent %s with %d item(s)/%d variation(s), size=%s.",
                subevent_id, len(item_ids), len(variation_ids), max_participants,
            )
            client.create_quota(
                organizer_slug=organizer_slug,
                event_slug=event_slug,
                payload=quota_payload,
            )

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
    def _resolve_variation_id(variations: list[dict], name_or_id: str | None) -> int | None:
        """Same matching rule as `_resolve_item_id`, against a variation's
        `value` (multi-lingual display name) instead of `name`."""
        if not name_or_id:
            return None
        if name_or_id.isdigit():
            return int(name_or_id)
        needle = name_or_id.strip().lower()
        for variation in variations:
            values = variation.get("value") or {}
            if any(v.strip().lower() == needle for v in values.values()):
                return int(variation["id"])
        return None

    @staticmethod
    def _resolve_item_and_variation(
        items: list[dict], item_name_or_id: str | None, variation_name_or_id: str | None,
    ) -> tuple[int | None, int | None]:
        """Resolve an §14 item binding's `item`/`variation` (each ID-or-name)
        against the live Pretix item list. Variations are matched within the
        resolved item's inline `variations` array."""
        item_id = PretixSyncItem._resolve_item_id(items, item_name_or_id)
        if item_id is None or not variation_name_or_id:
            return item_id, None
        item = next((i for i in items if int(i["id"]) == item_id), None)
        variations = (item or {}).get("variations") or []
        return item_id, PretixSyncItem._resolve_variation_id(variations, variation_name_or_id)

    @staticmethod
    def _build_binding_price_overrides(
        items_config: list[dict], items: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """§14: build (item_price_overrides, variation_price_overrides) from
        the resolved `payload["items"]` entries — each
        `{"item": ..., "variation": ..., "price": ...}`. `price` is a
        required binding in the schema, but its *resolved* value can still
        come back None (e.g. an effective key the policy didn't produce) —
        guarded defensively rather than assumed present."""
        item_overrides: list[dict] = []
        variation_overrides: list[dict] = []
        for entry in items_config:
            if not entry.get("item"):
                continue  # unfilled placeholder row — save always allows this, ignore on sync
            price = entry.get("price")
            if price is None:
                continue
            item_id, variation_id = PretixSyncItem._resolve_item_and_variation(
                items, entry.get("item"), entry.get("variation"),
            )
            if variation_id is not None:
                variation_overrides.append({"variation": variation_id, "price": str(price)})
            elif item_id is not None:
                item_overrides.append({"item": item_id, "price": str(price)})
            else:
                logger.warning(
                    "sync_pretix: could not resolve item/variation %r/%r for price override.",
                    entry.get("item"), entry.get("variation"),
                )
        return item_overrides, variation_overrides

    @staticmethod
    def _resolve_binding_quota_members(
        items_config: list[dict], items: list[dict],
    ) -> tuple[list[int], list[int]]:
        """§14: build (item_ids, variation_ids) for the subevent's shared
        quota — every entry in `payload["items"]` is a quota member, no
        opt-out; the item bindings list *is* the quota membership.

        Pretix rejects a quota whose `variations` includes a variation
        whose parent item isn't also present in `items`
        ("Alle Varianten müssen zu einem Produkt gehören, das auch in der
        Liste der Produkte enthalten ist.") — so a variation-scoped entry
        must add BOTH its parent item id (deduplicated; an item with
        several bound variations must only appear once) and the variation
        id, not the item id OR the variation id."""
        item_ids: list[int] = []
        variation_ids: list[int] = []
        for entry in items_config:
            if not entry.get("item"):
                continue  # unfilled placeholder row — save always allows this, ignore on sync
            item_id, variation_id = PretixSyncItem._resolve_item_and_variation(
                items, entry.get("item"), entry.get("variation"),
            )
            if item_id is None:
                logger.warning(
                    "sync_pretix: could not resolve item/variation %r/%r for quota membership.",
                    entry.get("item"), entry.get("variation"),
                )
                continue
            if item_id not in item_ids:
                item_ids.append(item_id)
            if variation_id is not None:
                variation_ids.append(variation_id)
        return item_ids, variation_ids

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
