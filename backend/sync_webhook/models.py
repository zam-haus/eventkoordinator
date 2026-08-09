"""sync_webhook: the generic HTTP push target (events-and-sync.md §3, Step 11).

Posts the effective-values snapshot (SyncBaseItem.synced_payload) verbatim as
JSON to a configured URL, with a bearer token and arbitrary constant custom
headers for auth. No payload templating, no request signing — receivers
adapt to the effective object's shape. The body also carries entity id,
target key, status, and a monotonically increasing sequence number so
receivers can order and deduplicate deliveries.
"""
from __future__ import annotations

from typing import ClassVar

import requests
from django.db import models

from sync_core.models import SyncBaseItem, SyncBaseTarget


class SyncWebhookTarget(SyncBaseTarget):
    """A generic HTTP(S) endpoint. Auth is Authorization: Bearer <token> plus
    arbitrary constant custom headers; both the token and the header values
    are secret_field_names since receivers may embed credentials in either."""

    #: Fields whose values must never be exposed through the public API.
    secret_field_names: ClassVar[list[str]] = ["bearer_token", "custom_headers"]

    url = models.URLField(max_length=2000)
    bearer_token = models.CharField(max_length=500, blank=True, default="")
    #: Constant header name -> value map, sent on every request verbatim.
    custom_headers = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name

    @classmethod
    def sync_item_model(cls):
        return SyncWebhookItem


class SyncWebhookItem(SyncBaseItem):
    """One (entity, webhook target) sync relationship. `sequence` increments
    on every push attempt (whether it ultimately succeeds or fails) so the
    receiver can order/dedupe deliveries even across retries."""

    sequence = models.PositiveIntegerField(default=0)

    def push(self) -> None:
        target = self.sync_target
        if not isinstance(target, SyncWebhookTarget):
            target = SyncWebhookTarget.objects.get(pk=target.pk)

        self.sequence += 1
        self.save(update_fields=["sequence"])

        payload = {
            "entity_id": str(self.related_entity_id),
            "target": target.key,
            "status": self.status,
            "sequence": self.sequence,
            "effective": self.synced_payload or {},
        }
        headers = {"Authorization": f"Bearer {target.bearer_token}"}
        headers.update(target.custom_headers or {})

        try:
            response = requests.post(target.url, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"sync_webhook push to {target.url!r} failed: {exc}") from exc

    def __str__(self):
        return f"SyncWebhookItem(entity={self.related_entity_id}, target={self.sync_target_id}, seq={self.sequence})"
