"""Tests for GET /calendar/ (events-and-sync.md §6, Step 9)."""
import json

from django.test import Client, TestCase, override_settings

from userdefinedmodel.tests.factories import (
    ALLOW_ALL_POLICY, FieldValueFactory, StaffUserFactory, make_entity_with_type, wrap_policy,
)
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE, _make_field_with_label

DENY_ALL_POLICY = wrap_policy("""
package udm
import rego.v1
allow := false
""")


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class CalendarEndpointTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.client = Client()
        self.staff = StaffUserFactory()
        self.client.force_login(self.staff)

    def get(self, start, end, sources):
        return self.client.get(f"/api/udm/calendar/?start={start}&end={end}&sources={sources}")

    def _entity_with_dates(self, start_val, end_val=None, policy_source=ALLOW_ALL_POLICY):
        entity, udm_type, version, _ = make_entity_with_type(policy_source=policy_source)
        start_field = _make_field_with_label(version, "start", "date", label="Start")
        FieldValueFactory(node=entity, field=start_field, value_date=start_val)
        if end_val is not None:
            end_field = _make_field_with_label(version, "end", "date", label="End")
            FieldValueFactory(node=entity, field=end_field, value_date=end_val)
        return entity, udm_type

    def test_entity_within_range_included(self):
        import datetime
        entity, udm_type = self._entity_with_dates(datetime.date(2026, 6, 15))
        resp = self.get("2026-06-01", "2026-06-30", f"{udm_type.id}:start:")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["entity_id"], str(entity.id))
        self.assertEqual(body[0]["source"], "udm")

    def test_entity_outside_range_excluded(self):
        import datetime
        _, udm_type = self._entity_with_dates(datetime.date(2026, 1, 1))
        resp = self.get("2026-06-01", "2026-06-30", f"{udm_type.id}:start:")
        self.assertEqual(json.loads(resp.content), [])

    def test_end_field_extends_range_match(self):
        import datetime
        entity, udm_type = self._entity_with_dates(datetime.date(2026, 5, 1), datetime.date(2026, 6, 10))
        resp = self.get("2026-06-01", "2026-06-30", f"{udm_type.id}:start:end")
        body = json.loads(resp.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["entity_id"], str(entity.id))

    def test_policy_denied_entity_omitted(self):
        import datetime
        self._entity_with_dates(datetime.date(2026, 6, 15), policy_source=DENY_ALL_POLICY)
        udm_type = None
        from userdefinedmodel.models import UserDefinedModelType
        udm_type = UserDefinedModelType.objects.latest("created_at")
        resp = self.get("2026-06-01", "2026-06-30", f"{udm_type.id}:start:")
        self.assertEqual(json.loads(resp.content), [])

    def test_invalid_date_400s(self):
        resp = self.get("not-a-date", "2026-06-30", "")
        self.assertEqual(resp.status_code, 400)

    def test_empty_sources_returns_empty(self):
        resp = self.get("2026-06-01", "2026-06-30", "")
        self.assertEqual(json.loads(resp.content), [])
