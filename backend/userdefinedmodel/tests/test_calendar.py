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

    def test_aware_datetime_field_does_not_raise(self):
        """datetime fields are stored aware under USE_TZ=True; naive query
        params must be normalized or this raises TypeError at comparison."""
        import datetime

        entity, udm_type, version, _ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        start_field = _make_field_with_label(version, "start", "datetime", label="Start")
        aware_start = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        FieldValueFactory(node=entity, field=start_field, value_datetime=aware_start)
        resp = self.get("2026-06-01", "2026-06-30", f"{udm_type.id}:start:")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["entity_id"], str(entity.id))

    def test_source_spec_returns_remote_calendar_entries_in_range(self):
        import datetime

        from sync_core.models import CalendarSource, RemoteCalendarEntry

        source = CalendarSource.objects.create(
            key="team-ical", name="Team", kind=CalendarSource.KIND_ICAL, url="https://x/feed.ics",
        )
        RemoteCalendarEntry.objects.create(
            source=source, uid="ev1", title="Standup",
            start=datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2026, 6, 15, 9, 30, tzinfo=datetime.timezone.utc),
        )
        RemoteCalendarEntry.objects.create(
            source=source, uid="ev2", title="Out of range",
            start=datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2026, 1, 1, 9, 30, tzinfo=datetime.timezone.utc),
        )
        resp = self.get("2026-06-01", "2026-06-30", "source:team-ical")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["source"], "team-ical")
        self.assertEqual(body[0]["title"], "Standup")
        self.assertIsNone(body[0]["entity_id"])

    def test_source_spec_disabled_source_excluded(self):
        import datetime

        from sync_core.models import CalendarSource, RemoteCalendarEntry

        source = CalendarSource.objects.create(
            key="disabled-ical", name="Disabled", kind=CalendarSource.KIND_ICAL,
            url="https://x/feed.ics", enabled=False,
        )
        RemoteCalendarEntry.objects.create(
            source=source, uid="ev1", title="Hidden",
            start=datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2026, 6, 15, 9, 30, tzinfo=datetime.timezone.utc),
        )
        resp = self.get("2026-06-01", "2026-06-30", "source:disabled-ical")
        self.assertEqual(json.loads(resp.content), [])

    def test_mixed_udm_and_source_specs(self):
        import datetime

        from sync_core.models import CalendarSource, RemoteCalendarEntry

        entity, udm_type = self._entity_with_dates(datetime.date(2026, 6, 15))
        source = CalendarSource.objects.create(
            key="mix-ical", name="Mix", kind=CalendarSource.KIND_ICAL, url="https://x/feed.ics",
        )
        RemoteCalendarEntry.objects.create(
            source=source, uid="ev1", title="Remote",
            start=datetime.datetime(2026, 6, 16, 9, 0, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2026, 6, 16, 9, 30, tzinfo=datetime.timezone.utc),
        )
        resp = self.get("2026-06-01", "2026-06-30", f"{udm_type.id}:start:,source:mix-ical")
        body = json.loads(resp.content)
        self.assertEqual({e["source"] for e in body}, {"udm", "mix-ical"})

    def test_source_spec_echoes_spec(self):
        import datetime

        from sync_core.models import CalendarSource, RemoteCalendarEntry

        source = CalendarSource.objects.create(
            key="spec-ical", name="Spec", kind=CalendarSource.KIND_ICAL, url="https://x/feed.ics",
        )
        RemoteCalendarEntry.objects.create(
            source=source, uid="ev1", title="Standup",
            start=datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2026, 6, 15, 9, 30, tzinfo=datetime.timezone.utc),
        )
        resp = self.get("2026-06-01", "2026-06-30", "source:spec-ical")
        body = json.loads(resp.content)
        self.assertEqual(body[0]["spec"], "source:spec-ical")

    def test_udm_spec_echoes_spec(self):
        import datetime
        entity, udm_type = self._entity_with_dates(datetime.date(2026, 6, 15))
        spec = f"{udm_type.id}:start:"
        resp = self.get("2026-06-01", "2026-06-30", spec)
        body = json.loads(resp.content)
        self.assertEqual(body[0]["spec"], spec)


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class SubmodelCalendarSpecTests(TestCase):
    """events-and-sync.md §6.1/Step 10: `submodel:<entity>:<field>(<start>[,<end>])`."""

    databases = ["default"]

    def setUp(self):
        from userdefinedmodel.models import (
            ConfigLanguage, ConfigVersion, FieldConfig,
        )
        from userdefinedmodel.tests.factories import (
            StaffUserFactory, UserDefinedModelEntityFactory, UserDefinedModelTypeFactory,
        )

        self.client = Client()
        self.staff = StaffUserFactory()
        self.client.force_login(self.staff)

        self.sub_config = FieldConfig.objects.create(name="Timeslot Sub Config")
        ConfigLanguage.objects.create(config=self.sub_config, code="en", label="English", is_default=True)
        self.sub_version = ConfigVersion.objects.create(config=self.sub_config, status="published")
        _make_field_with_label(self.sub_version, "start", "datetime", label="Start")
        _make_field_with_label(self.sub_version, "end", "datetime", label="End")

        self.config = FieldConfig.objects.create(name="Event-like Root Config")
        ConfigLanguage.objects.create(config=self.config, code="en", label="English", is_default=True)
        self.version = ConfigVersion.objects.create(config=self.config, status="published")
        _make_field_with_label(
            self.version, "timeslots", "submodel_list", label="Timeslots",
            submodel_config=self.sub_version,
        )

        self.udm_type = UserDefinedModelTypeFactory(name="Event-like Type", field_config=self.config)
        self.entity = UserDefinedModelEntityFactory(
            config_version=self.version, user_defined_model_type=self.udm_type
        )

    def _create_timeslot(self, start, end):
        resp = self.client.patch(
            f"/api/udm/entities/{self.entity.id}/",
            data=json.dumps({"changed_fields": {
                "timeslots": [{"op": "create", "fields": {
                    "start": start.isoformat(), "end": end.isoformat(),
                }}],
            }}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()["children"]["timeslots"][0]["id"]

    def get(self, start, end, sources):
        return self.client.get(f"/api/udm/calendar/?start={start}&end={end}&sources={sources}")

    def test_range_form_returns_child_entry(self):
        import datetime
        start = datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        child_id = self._create_timeslot(start, end)

        spec = f"submodel:{self.entity.id}:timeslots(start,end)"
        resp = self.get("2026-06-01", "2026-06-30", spec)
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["source"], "submodel")
        self.assertEqual(body[0]["entity_id"], child_id)
        self.assertEqual(body[0]["spec"], spec)
        self.assertEqual(body[0]["start"], start.isoformat())
        self.assertEqual(body[0]["end"], end.isoformat())

    def test_range_form_out_of_range_excluded(self):
        import datetime
        start = datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc)
        self._create_timeslot(start, end)

        spec = f"submodel:{self.entity.id}:timeslots(start,end)"
        resp = self.get("2026-06-01", "2026-06-30", spec)
        self.assertEqual(json.loads(resp.content), [])

    def test_point_in_time_single_slug_form(self):
        import datetime
        start = datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        self._create_timeslot(start, end)

        spec = f"submodel:{self.entity.id}:timeslots(start)"
        resp = self.get("2026-06-01", "2026-06-30", spec)
        body = json.loads(resp.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["start"], start.isoformat())
        self.assertEqual(body[0]["end"], start.isoformat())

    def test_unknown_entity_returns_empty(self):
        import uuid
        spec = f"submodel:{uuid.uuid4()}:timeslots(start,end)"
        resp = self.get("2026-06-01", "2026-06-30", spec)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content), [])

    def test_policy_denied_root_omits_children(self):
        import datetime
        from userdefinedmodel.models import Policy, UserDefinedModelTypePolicy

        start = datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        self._create_timeslot(start, end)

        UserDefinedModelTypePolicy.objects.filter(user_defined_model_type=self.udm_type).delete()
        deny_policy = Policy.objects.create(slug="deny-all-timeslot-test", source=DENY_ALL_POLICY)
        UserDefinedModelTypePolicy.objects.create(
            user_defined_model_type=self.udm_type, policy=deny_policy, sort_order=0,
        )

        spec = f"submodel:{self.entity.id}:timeslots(start,end)"
        resp = self.get("2026-06-01", "2026-06-30", spec)
        self.assertEqual(json.loads(resp.content), [])

    def test_malformed_submodel_spec_in_config_rejected(self):
        from ninja.errors import ValidationError as NinjaValidationError

        from userdefinedmodel.schemas import CalendarTypeConfig

        with self.assertRaises((ValueError, NinjaValidationError)):
            CalendarTypeConfig(sources=["submodel:bad-no-parens"])

    def test_type_wide_form_returns_children_of_every_entity_of_type(self):
        import datetime

        from userdefinedmodel.tests.factories import UserDefinedModelEntityFactory

        start1 = datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc)
        end1 = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        child1 = self._create_timeslot(start1, end1)

        other_entity = UserDefinedModelEntityFactory(
            config_version=self.version, user_defined_model_type=self.udm_type,
        )
        resp = self.client.patch(
            f"/api/udm/entities/{other_entity.id}/",
            data=json.dumps({"changed_fields": {
                "timeslots": [{"op": "create", "fields": {
                    "start": "2026-06-16T09:00:00+00:00", "end": "2026-06-16T10:00:00+00:00",
                }}],
            }}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        child2 = resp.json()["children"]["timeslots"][0]["id"]

        spec = f"submodel:{self.udm_type.id}:timeslots(start,end)"
        resp = self.get("2026-06-01", "2026-06-30", spec)
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual({e["entity_id"] for e in body}, {child1, child2})
