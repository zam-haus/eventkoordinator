"""Pretix REST API client shared between model push logic and management commands."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


class PretixApiError(Exception):
    """Raised when a Pretix API request fails."""


def _normalize_api_base_url(url: str) -> str:
    return url.rstrip("/")


class PretixApiClient:
    def __init__(self, *, api_base_url: str, token: str, timeout_seconds: float = 15.0):
        self.api_base_url = _normalize_api_base_url(api_base_url)
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.token = token
        self.session.headers.update(self._get_headers(token))

    def _get_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.api_base_url}{path}"

    def _request(self, method: str, path: str, *, json_payload: dict | None = None) -> dict:
        try:
            response = self.session.request(
                method,
                self._url(path),
                json=json_payload,
                timeout=self.timeout_seconds,
                headers=self._get_headers(self.token),
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else ""
            raise PretixApiError(
                f"Pretix API request failed for {method} {path}: {exc} – response: {body}"
            ) from exc
        except requests.RequestException as exc:
            raise PretixApiError(
                f"Pretix API request failed for {method} {path}: {exc}"
            ) from exc

    def _request_paginated(self, path: str) -> list[dict]:
        payload = self._request("GET", path)
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise PretixApiError(
                f"Unexpected Pretix API response for {path}: {payload!r}"
            )
        results = list(payload.get("results") or [])
        next_url = payload.get("next")
        while next_url:
            try:
                response = self.session.get(next_url, timeout=self.timeout_seconds)
                response.raise_for_status()
                page = response.json()
            except requests.RequestException as exc:
                raise PretixApiError(
                    f"Pretix API pagination failed for {path}: {exc}"
                ) from exc
            if not isinstance(page, dict):
                raise PretixApiError(
                    f"Unexpected Pretix API response page for {path}: {page!r}"
                )
            results.extend(page.get("results") or [])
            next_url = page.get("next")
        return results

    # ------------------------------------------------------------------ #
    # Organizer                                                            #
    # ------------------------------------------------------------------ #

    def get_organizer(self, organizer_slug: str) -> dict | None:
        organizers = self._request_paginated("/organizers/")
        for organizer in organizers:
            if organizer.get("slug") == organizer_slug:
                return organizer
        return None

    def create_organizer(self, *, slug: str, name: str) -> dict:
        return self._request(
            "POST", "/organizers/", json_payload={"slug": slug, "name": name}
        )

    def patch_organizer(self, *, slug: str, payload: dict) -> dict:
        return self._request("PATCH", f"/organizers/{slug}/", json_payload=payload)

    # ------------------------------------------------------------------ #
    # Events                                                               #
    # ------------------------------------------------------------------ #

    def list_events(self, organizer_slug: str) -> list[dict]:
        return self._request_paginated(f"/organizers/{organizer_slug}/events/")

    def get_event(self, *, organizer_slug: str, event_slug: str) -> dict:
        return self._request("GET", f"/organizers/{organizer_slug}/events/{event_slug}/")

    def create_event(self, *, organizer_slug: str, payload: dict) -> dict:
        return self._request(
            "POST", f"/organizers/{organizer_slug}/events/", json_payload=payload
        )

    def patch_event(self, *, organizer_slug: str, event_slug: str, payload: dict) -> dict:
        return self._request(
            "PATCH",
            f"/organizers/{organizer_slug}/events/{event_slug}/",
            json_payload=payload,
        )

    # ------------------------------------------------------------------ #
    # Items (ticket products)                                              #
    # ------------------------------------------------------------------ #

    def list_items(self, *, organizer_slug: str, event_slug: str) -> list[dict]:
        return self._request_paginated(
            f"/organizers/{organizer_slug}/events/{event_slug}/items/"
        )

    def create_item(self, *, organizer_slug: str, event_slug: str, payload: dict) -> dict:
        return self._request(
            "POST",
            f"/organizers/{organizer_slug}/events/{event_slug}/items/",
            json_payload=payload,
        )

    # ------------------------------------------------------------------ #
    # Item variations                                                      #
    # ------------------------------------------------------------------ #

    def list_item_variations(
        self, *, organizer_slug: str, event_slug: str, item_id: int
    ) -> list[dict]:
        return self._request_paginated(
            f"/organizers/{organizer_slug}/events/{event_slug}/items/{item_id}/variations/"
        )

    # ------------------------------------------------------------------ #
    # Subevents                                                            #
    # ------------------------------------------------------------------ #

    def create_subevent(
        self, *, organizer_slug: str, event_slug: str, payload: dict
    ) -> dict:
        return self._request(
            "POST",
            f"/organizers/{organizer_slug}/events/{event_slug}/subevents/",
            json_payload=payload,
        )

    def get_subevent(
        self, *, organizer_slug: str, event_slug: str, subevent_id: str
    ) -> dict:
        return self._request(
            "GET",
            f"/organizers/{organizer_slug}/events/{event_slug}/subevents/{subevent_id}/",
        )

    def patch_subevent(
        self,
        *,
        organizer_slug: str,
        event_slug: str,
        subevent_id: str,
        payload: dict,
    ) -> dict:
        return self._request(
            "PATCH",
            f"/organizers/{organizer_slug}/events/{event_slug}/subevents/{subevent_id}/",
            json_payload=payload,
        )

    def delete_subevent(
        self, *, organizer_slug: str, event_slug: str, subevent_id: str
    ) -> None:
        self._request(
            "DELETE",
            f"/organizers/{organizer_slug}/events/{event_slug}/subevents/{subevent_id}/",
        )

    # ------------------------------------------------------------------ #
    # Quotas                                                               #
    # ------------------------------------------------------------------ #

    def list_quotas(
        self,
        *,
        organizer_slug: str,
        event_slug: str,
        subevent_id: str | None = None,
    ) -> list[dict]:
        path = f"/organizers/{organizer_slug}/events/{event_slug}/quotas/"
        if subevent_id is not None:
            path += f"?subevent={subevent_id}"
        return self._request_paginated(path)

    def create_quota(
        self, *, organizer_slug: str, event_slug: str, payload: dict
    ) -> dict:
        return self._request(
            "POST",
            f"/organizers/{organizer_slug}/events/{event_slug}/quotas/",
            json_payload=payload,
        )

    def patch_quota(
        self, *, organizer_slug: str, event_slug: str, quota_id: str, payload: dict
    ) -> dict:
        return self._request(
            "PATCH",
            f"/organizers/{organizer_slug}/events/{event_slug}/quotas/{quota_id}/",
            json_payload=payload,
        )

