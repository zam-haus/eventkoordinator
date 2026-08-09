from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from apiv1.models.basedata import ProposalArea
from sync_pretix.models import PretixSyncTarget, PretixSyncTargetAreaAssociation
from sync_pretix.pretix_client import PretixApiClient, PretixApiError

logger = logging.getLogger(__name__)



def _build_event_slug(prefix: str, area_code: str) -> str:
    slug = slugify(f"{prefix}-{area_code}")
    if not slug:
        raise CommandError(
            f"Could not build a valid pretix event slug for area code {area_code!r}."
        )
    return slug[:64]


@dataclass
class PretixSettings:
    api_base_url: str
    event_currency: str
    event_timezone: str
    event_locale: str
    api_token: str | None = None
    organizer_slug: str = "zam"
    organizer_name: str = "ZAM"
    event_slug_prefix: str = "area-"



class Command(BaseCommand):
    help = "Ensure one pretix organizer and one pretix event per active proposal area."

    def add_arguments(self, parser):
        parser.add_argument("--api-base-url", type=str, default=None)
        parser.add_argument("--api-token", type=str, default=None)
        parser.add_argument("--organizer-slug", type=str, default=None)
        parser.add_argument("--organizer-name", type=str, default=None)
        parser.add_argument("--event-slug-prefix", type=str, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def _read_settings(self, options: dict) -> PretixSettings:
        return PretixSettings(
            api_base_url=options.get("api_base_url") or settings.PRETIX_API_BASE_URL,
            api_token=options.get("api_token") or getattr(settings, "PRETIX_API_TOKEN", None),
            organizer_slug=options.get("organizer_slug") or getattr(settings, "PRETIX_ORGANIZER_SLUG", "zam"),
            organizer_name=options.get("organizer_name") or getattr(settings, "PRETIX_ORGANIZER_NAME", "ZAM"),
            event_slug_prefix=options.get("event_slug_prefix") or getattr(settings, "PRETIX_EVENT_SLUG_PREFIX", "area"),
            event_currency=getattr(settings, "PRETIX_EVENT_CURRENCY", "EUR"),
            event_timezone=getattr(settings, "PRETIX_EVENT_TIMEZONE", "Europe/Berlin"),
            event_locale=getattr(settings, "PRETIX_EVENT_LOCALE", "en"),
        )

    def _build_client(self, runtime_settings: PretixSettings) -> PretixApiClient:
        if not runtime_settings.api_token:
            self._setup_pretix(runtime_settings)
        return PretixApiClient(
            api_base_url=runtime_settings.api_base_url,
            token=runtime_settings.api_token,
        )

    def _event_payload(
        self, *, runtime_settings: PretixSettings, event_slug: str, area_label: str
    ) -> dict:
        today = date.today()
        tomorrow = today + timedelta(days=1)
        return {
            "slug": event_slug,
            "name": {runtime_settings.event_locale: area_label},
            "currency": runtime_settings.event_currency,
            "timezone": runtime_settings.event_timezone,
            "date_from": today.isoformat(),
            "date_to": tomorrow.isoformat(),
            "live": False,
            "has_subevents": True,
        }

    def _replace_sync_target(
        self, *, runtime_settings: PretixSettings, dry_run: bool
    ) -> PretixSyncTarget | None:
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "[dry-run] Would replace all PretixSyncTarget entries with current client setup values."
                )
            )
            return PretixSyncTarget.objects.order_by("-created_at").first()

        PretixSyncTarget.objects.all().delete()
        return PretixSyncTarget.objects.create(
            key=f"pretix:{runtime_settings.organizer_slug}",
            name=f"Pretix ({runtime_settings.organizer_slug})",
            api_url=runtime_settings.api_base_url,
            api_token=runtime_settings.api_token,
            organizer_slug=runtime_settings.organizer_slug,
        )

    def _default_ticket_product_names(self) -> list[str]:
        return [
            PretixSyncTargetAreaAssociation._meta.get_field(
                "ticket_product_member_regular_id"
            ).get_default(),
            PretixSyncTargetAreaAssociation._meta.get_field(
                "ticket_product_member_discounted_id"
            ).get_default(),
            PretixSyncTargetAreaAssociation._meta.get_field(
                "ticket_product_guest_regular_id"
            ).get_default(),
            PretixSyncTargetAreaAssociation._meta.get_field(
                "ticket_product_guest_discounted_id"
            ).get_default(),
            PretixSyncTargetAreaAssociation._meta.get_field(
                "ticket_product_business_id"
            ).get_default(),
        ]

    def _ensure_default_ticket_products(
        self,
        *,
        client: PretixApiClient,
        runtime_settings: PretixSettings,
        event_slug: str,
        dry_run: bool,
        skip_existing_lookup: bool = False,
    ) -> int:
        existing_names: set[str] = set()
        if not skip_existing_lookup:
            existing_items = client.list_items(
                organizer_slug=runtime_settings.organizer_slug,
                event_slug=event_slug,
            )
            existing_names = {
                localized_name
                for item in existing_items
                for localized_name in (item.get("name") or {}).values()
                if isinstance(localized_name, str)
            }

        created_count = 0
        for product_name in self._default_ticket_product_names():
            if not product_name or product_name in existing_names:
                continue

            item_payload = {
                "name": {runtime_settings.event_locale: product_name},
                "default_price": "0.00",
                "admission": True,
            }
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"[dry-run] Would create ticket product {product_name!r} in event {event_slug!r}."
                    )
                )
            else:
                client.create_item(
                    organizer_slug=runtime_settings.organizer_slug,
                    event_slug=event_slug,
                    payload=item_payload,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created ticket product {product_name!r} in event {event_slug!r}."
                    )
                )
            created_count += 1
        return created_count

    def handle(self, *args, **options):
        try:
            self._run(*args, **options)
        except PretixApiError as exc:
            raise CommandError(str(exc)) from exc

    def _run(self, *args, **options):
        runtime_settings = self._read_settings(options)

        dry_run = bool(options.get("dry_run"))
        client = self._build_client(runtime_settings)
        sync_target = self._replace_sync_target(
            runtime_settings=runtime_settings, dry_run=dry_run
        )

        organizer = client.get_organizer(runtime_settings.organizer_slug)
        if organizer is None:
            raise Exception("Failed to get organizer from pretix.")
        elif organizer.get("name") != runtime_settings.organizer_name:
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"[dry-run] Would update organizer name to {runtime_settings.organizer_name!r}."
                    )
                )
            else:
                client.patch_organizer(
                    slug=runtime_settings.organizer_slug,
                    payload={"name": runtime_settings.organizer_name},
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated organizer {runtime_settings.organizer_slug!r} name."
                    )
                )

        area_rows = list(
            ProposalArea.objects.filter(is_active=True).order_by("sort_order", "label")
        )
        if not area_rows:
            self.stdout.write(
                self.style.WARNING("No active proposal areas found. Nothing to sync.")
            )
            return

        existing_events = {
            event.get("slug"): event
            for event in client.list_events(runtime_settings.organizer_slug)
            if event.get("slug")
        }

        created_events = 0
        updated_events = 0
        reused_events = 0
        associations_created = 0
        ticket_products_created = 0

        for area in area_rows:
            event_slug = _build_event_slug(
                runtime_settings.event_slug_prefix, area.code
            )
            expected_name = {runtime_settings.event_locale: area.label}
            payload = self._event_payload(
                runtime_settings=runtime_settings,
                event_slug=event_slug,
                area_label=area.label,
            )
            existing = existing_events.get(event_slug)

            if existing is None:
                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(
                            f"[dry-run] Would create event {event_slug!r} for area {area.code!r}."
                        )
                    )
                else:
                    client.create_event(
                        organizer_slug=runtime_settings.organizer_slug,
                        payload=payload,
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Created event {event_slug!r} for area {area.code!r}."
                        )
                    )
                created_events += 1
            else:
                patch_payload = {}
                current_name = existing.get("name")
                if current_name != expected_name:
                    patch_payload["name"] = expected_name

                if not existing.get("has_subevents"):
                    patch_payload["has_subevents"] = True

                if patch_payload:
                    if dry_run:
                        self.stdout.write(
                            self.style.WARNING(
                                f"[dry-run] Would update event {event_slug!r} with payload {patch_payload!r}."
                            )
                        )
                    else:
                        client.patch_event(
                            organizer_slug=runtime_settings.organizer_slug,
                            event_slug=event_slug,
                            payload=patch_payload,
                        )
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Updated event {event_slug!r} for area {area.code!r}."
                            )
                        )
                    updated_events += 1
                else:
                    reused_events += 1

            if dry_run:
                # area_code is a plain string now (events-and-sync.md §3, Step 11:
                # PretixSyncTargetAreaAssociation decoupled from apiv1.ProposalArea),
                # so this is a direct filter rather than a reverse FK accessor.
                existing_association_query = PretixSyncTargetAreaAssociation.objects.filter(
                    area_code=area.code,
                )
                if sync_target is not None:
                    existing_association_query = existing_association_query.filter(
                        sync_target=sync_target
                    )
                existing_association = existing_association_query.order_by("-created_at").first()
                if existing_association is None:
                    self.stdout.write(
                        self.style.WARNING(
                            f"[dry-run] Would create area association for {area.code!r} to event {event_slug!r}."
                        )
                    )
                    associations_created += 1
                elif existing_association.event_slug != event_slug:
                    self.stdout.write(
                        self.style.WARNING(
                            f"[dry-run] Would update area association for {area.code!r} to event {event_slug!r}."
                        )
                    )
            else:
                association, association_created = PretixSyncTargetAreaAssociation.objects.get_or_create(
                    area_code=area.code,
                    sync_target=sync_target,
                    defaults={"event_slug": event_slug},
                )
                if association_created:
                    associations_created += 1
                elif association.event_slug != event_slug:
                    association.event_slug = event_slug
                    association.save(update_fields=["event_slug", "updated_at"])

            ticket_products_created += self._ensure_default_ticket_products(
                client=client,
                runtime_settings=runtime_settings,
                event_slug=event_slug,
                dry_run=dry_run,
                skip_existing_lookup=bool(dry_run and existing is None),
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Pretix sync finished: "
                f"created={created_events}, updated={updated_events}, unchanged={reused_events}, "
                f"associations_created={associations_created}, ticket_products_created={ticket_products_created}."
            )
        )

    def _setup_pretix(self, runtime_settings: PretixSettings):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            home_url = runtime_settings.api_base_url.rstrip("/").rstrip("/api/v1")
            page.goto(home_url)

            page.get_by_role("link", name="head over here").click()
            page.get_by_role("textbox", name="Email").fill("admin@localhost")
            page.get_by_role("textbox", name="Password").fill("admin")
            page.get_by_role("button", name="Log in").click()
            page.get_by_role("link", name=" Organizers").click()
            page.get_by_role("button", name=" Admin mode").click()
            page.get_by_role("link", name=" Create a new organizer").click()
            page.get_by_role("textbox", name="Name").click()
            number = str(random.randint(1, 999999))
            runtime_settings.organizer_slug = runtime_settings.organizer_slug + number
            runtime_settings.organizer_name = runtime_settings.organizer_name + number
            page.get_by_role("textbox", name="Name").fill(
                runtime_settings.organizer_name
            )
            page.get_by_role("textbox", name="Short form").fill(
                runtime_settings.organizer_slug
            )
            page.get_by_role("button", name="Save").click()
            page.get_by_role("link", name=" Teams").click()
            page.get_by_role("link").filter(has_text=re.compile(r"^$")).nth(2).click()
            apitokenname = "APITOKEN" + number
            page.get_by_role("textbox", name="Token name").fill("%s" % apitokenname)
            page.get_by_role(
                "row", name=("Token name %s  Add" % apitokenname)
            ).get_by_role("button").click()
            locator = page.get_by_text("A new API token has been")
            expect(locator).to_be_visible()
            page.wait_for_timeout(500)
            apitoken_message = locator.inner_html()
            logger.info(apitoken_message)
            apitoken_message_strip = apitoken_message.strip()
            message_strip__lstrip = apitoken_message_strip.removeprefix(
                "A new API token has been created with the following secret:"
            )
            apitoken = message_strip__lstrip.split("<br>")[0]
            runtime_settings.api_token = apitoken
            logger.info(runtime_settings.api_token)
            if (
                not 55 < len(runtime_settings.api_token) < 70
            ):  # sometimes 64, sometimes 61 chars
                raise Exception("Invalid API token")
            page.close()
            browser.close()
