"""Tests for sync_webhook (events-and-sync.md §3, Step 11): payload shape,
headers, and status transitions via the sync_core worker."""
from unittest.mock import Mock, patch

from django.test import TestCase

from sync_core.models import DERIVED_STATE_ERROR, DERIVED_STATE_PENDING, DERIVED_STATE_SYNCED
from sync_core.tasks import push_pending_sync_items
from sync_webhook.models import SyncWebhookItem, SyncWebhookTarget
from userdefinedmodel.tests.factories import make_entity_with_type


class SyncWebhookTargetTests(TestCase):
    def test_secret_field_names(self):
        target = SyncWebhookTarget.objects.create(
            key="webhook:main", name="Main webhook", url="https://example.com/hook",
            bearer_token="s3cr3t", custom_headers={"X-Api-Key": "also-secret"},
        )
        self.assertEqual(set(target.secret_field_names), {"bearer_token", "custom_headers"})


class SyncWebhookItemPushTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.entity, *_ = make_entity_with_type()
        self.target = SyncWebhookTarget.objects.create(
            key="webhook:main", name="Main webhook", url="https://example.com/hook",
            bearer_token="s3cr3t-token", custom_headers={"X-Custom": "abc", "X-Other": "def"},
        )
        self.effective = {"title": "Some event", "status": "confirmed"}

    def _item(self, **kwargs):
        kwargs.setdefault("status", DERIVED_STATE_PENDING)
        kwargs.setdefault("synced_payload", self.effective)
        return SyncWebhookItem.objects.create(
            related_entity=self.entity, sync_target=self.target, **kwargs,
        )

    @patch("sync_webhook.models.requests.post")
    def test_push_sends_payload_and_headers(self, mock_post):
        mock_post.return_value = Mock(status_code=200, raise_for_status=Mock())
        item = self._item()

        item.push()

        mock_post.assert_called_once()
        _args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["entity_id"], str(self.entity.id))
        self.assertEqual(kwargs["json"]["target"], "webhook:main")
        self.assertEqual(kwargs["json"]["status"], DERIVED_STATE_PENDING)
        self.assertEqual(kwargs["json"]["sequence"], 1)
        self.assertEqual(kwargs["json"]["effective"], self.effective)

        headers = kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer s3cr3t-token")
        self.assertEqual(headers["X-Custom"], "abc")
        self.assertEqual(headers["X-Other"], "def")

    @patch("sync_webhook.models.requests.post")
    def test_sequence_increments_across_pushes(self, mock_post):
        mock_post.return_value = Mock(status_code=200, raise_for_status=Mock())
        item = self._item()

        item.push()
        item.push()

        self.assertEqual(mock_post.call_args_list[0].kwargs["json"]["sequence"], 1)
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["sequence"], 2)
        item.refresh_from_db()
        self.assertEqual(item.sequence, 2)

    @patch("sync_webhook.models.requests.post")
    def test_push_failure_raises(self, mock_post):
        import requests

        mock_response = Mock(status_code=500)
        mock_response.raise_for_status.side_effect = requests.HTTPError("boom")
        mock_post.return_value = mock_response
        item = self._item()

        with self.assertRaises(RuntimeError):
            item.push()

        # Sequence still advanced — a failed attempt still consumed a slot.
        item.refresh_from_db()
        self.assertEqual(item.sequence, 1)


class SyncWebhookWorkerTests(TestCase):
    """End-to-end through push_pending_sync_items (sync_core/tasks.py),
    which owns status/synced_at/last_error transitions polymorphically."""

    databases = ["default"]

    def setUp(self):
        self.entity, *_ = make_entity_with_type()
        self.target = SyncWebhookTarget.objects.create(
            key="webhook:main", name="Main webhook", url="https://example.com/hook",
            bearer_token="tok",
        )

    @patch("sync_webhook.models.requests.post")
    def test_success_marks_synced(self, mock_post):
        mock_post.return_value = Mock(status_code=200, raise_for_status=Mock())
        item = SyncWebhookItem.objects.create(
            related_entity=self.entity, sync_target=self.target,
            status=DERIVED_STATE_PENDING, synced_payload={"a": 1},
        )

        result = push_pending_sync_items()

        item.refresh_from_db()
        self.assertEqual(result, {"pushed": 1, "failed": 0})
        self.assertEqual(item.status, DERIVED_STATE_SYNCED)
        self.assertIsNotNone(item.synced_at)
        self.assertEqual(item.last_error, "")

    @patch("sync_webhook.models.requests.post")
    def test_failure_marks_error(self, mock_post):
        import requests

        mock_response = Mock(status_code=503)
        mock_response.raise_for_status.side_effect = requests.HTTPError("service unavailable")
        mock_post.return_value = mock_response
        item = SyncWebhookItem.objects.create(
            related_entity=self.entity, sync_target=self.target,
            status=DERIVED_STATE_PENDING, synced_payload={"a": 1},
        )

        result = push_pending_sync_items()

        item.refresh_from_db()
        self.assertEqual(result, {"pushed": 0, "failed": 1})
        self.assertEqual(item.status, DERIVED_STATE_ERROR)
        self.assertIn("service unavailable", item.last_error)
