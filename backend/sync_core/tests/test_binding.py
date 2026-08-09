"""Tests for field binding resolution (events-and-sync.md Step 13.2,
Step 14's `resolve_deep` for nested binding sources, and Step 13.3's
`resolve_submodel_slots` fan-out)."""
from django.test import TestCase

from sync_core.binding import BindingSource, SubmodelSpec, resolve_bindings, resolve_deep, resolve_submodel_slots
from userdefinedmodel.tests.factories import make_entity_with_type


class ResolveBindingsTests(TestCase):
    databases = ["default"]

    def test_effective_source(self):
        entity, *_ = make_entity_with_type()
        resolved = resolve_bindings(
            {"SUMMARY": BindingSource(effective="title")},
            entity=entity, effective={"title": "From policy"},
        )
        self.assertEqual(resolved, {"SUMMARY": "From policy"})

    def test_field_source_reads_stored_value(self):
        from userdefinedmodel.writer import apply_patch
        from userdefinedmodel.tests.factories import StaffUserFactory, ALLOW_ALL_POLICY
        from userdefinedmodel.models import Policy, UserDefinedModelTypePolicy

        entity, udm_type, *_ = make_entity_with_type()
        user = StaffUserFactory()
        p = Policy.objects.create(slug=f"allow-{entity.id}", source=ALLOW_ALL_POLICY)
        UserDefinedModelTypePolicy.objects.create(user_defined_model_type=udm_type, policy=p, sort_order=0)
        apply_patch(entity, {"title": "Stored value"}, user)
        entity.refresh_from_db()

        resolved = resolve_bindings(
            {"LOCATION": BindingSource(field="title")},
            entity=entity, effective={},
        )
        self.assertEqual(resolved, {"LOCATION": "Stored value"})

    def test_template_source_renders_jinja(self):
        entity, *_ = make_entity_with_type()
        resolved = resolve_bindings(
            {"DESCRIPTION": BindingSource(template="{{ effective.title }} ({{ entity.id }})")},
            entity=entity, effective={"title": "Talk"},
        )
        self.assertEqual(resolved["DESCRIPTION"], f"Talk ({entity.id})")

    def test_exactly_one_source_required(self):
        with self.assertRaises(Exception):
            BindingSource(effective="title", field="title")
        with self.assertRaises(Exception):
            BindingSource()

    def test_missing_key_reads_none(self):
        entity, *_ = make_entity_with_type()
        resolved = resolve_bindings(
            {"SUMMARY": BindingSource(effective="missing")},
            entity=entity, effective={},
        )
        self.assertIsNone(resolved["SUMMARY"])


class ResolveDeepTests(TestCase):
    """events-and-sync.md Step 14: `resolve_deep` resolves BindingSource-
    shaped dicts nested anywhere inside a richer structure (sync_pretix's
    per-item price override), leaving everything else — including sibling
    string/bool literals in the same dict — untouched."""
    databases = ["default"]

    def test_scalar_passthrough(self):
        entity, *_ = make_entity_with_type()
        self.assertEqual(resolve_deep("literal", entity=entity, effective={}), "literal")
        self.assertIsNone(resolve_deep(None, entity=entity, effective={}))
        self.assertEqual(resolve_deep(True, entity=entity, effective={}), True)

    def test_top_level_binding_source_resolved(self):
        entity, *_ = make_entity_with_type()
        resolved = resolve_deep({"effective": "event_slug"}, entity=entity, effective={"event_slug": "area-metal"})
        self.assertEqual(resolved, "area-metal")

    def test_nested_binding_source_resolved_sibling_literals_untouched(self):
        entity, *_ = make_entity_with_type()
        value = {
            "item": "Regular", "variation": None, "in_quota": True,
            "price": {"effective": "price_regular"},
        }
        resolved = resolve_deep(value, entity=entity, effective={"price_regular": "17.00"})
        self.assertEqual(resolved, {
            "item": "Regular", "variation": None, "in_quota": True, "price": "17.00",
        })

    def test_list_of_nested_binding_sources(self):
        entity, *_ = make_entity_with_type()
        value = [
            {"item": "Regular", "price": {"effective": "price_regular"}},
            {"item": "Student", "price": None},
        ]
        resolved = resolve_deep(value, entity=entity, effective={"price_regular": "17.00"})
        self.assertEqual(resolved, [
            {"item": "Regular", "price": "17.00"},
            {"item": "Student", "price": None},
        ])


class MarkSyncBindingIntegrationTests(TestCase):
    """mark_sync and recompute_staleness must resolve through the same
    bindings-aware function, or staleness compares mismatched shapes
    (events-and-sync.md Step 13.2)."""
    databases = ["default"]

    def _setup(self):
        from userdefinedmodel.models import TypeEditorTabConfig
        from sync_caldav.models import CalDAVSyncTarget

        entity, udm_type, version, config = make_entity_with_type()
        target = CalDAVSyncTarget.objects.create(
            key="caldav:main", name="Main", enabled=True,
            url="https://example.org/dav/", username="u", password="p",
            calendar_display_name="Main",
        )
        TypeEditorTabConfig.objects.create(
            config_version=version, tab_id="sync_targets", config={"target_keys": ["caldav:main"]},
        )
        TypeEditorTabConfig.objects.create(
            config_version=version, tab_id="sync_caldav",
            config={"bindings": {"SUMMARY": {"effective": "title"}}},
        )
        return entity, target

    def test_mark_sync_stores_resolved_bindings_not_raw_effective(self):
        from sync_core.models import mark_sync

        entity, target = self._setup()
        item = mark_sync(entity.id, target.key, "pending", effective={"title": "Talk", "extra": "ignored"})
        self.assertEqual(item.synced_payload, {"SUMMARY": "Talk"})

    def test_recompute_staleness_compares_resolved_payloads(self):
        from sync_core.models import mark_sync, recompute_staleness
        from sync_core.models import SyncBaseItem

        entity, target = self._setup()
        item = mark_sync(entity.id, target.key, "pending", effective={"title": "Talk"})
        item.status = "synced"
        item.save(update_fields=["status"])

        # Same effective, resolved the same way -> not stale.
        recompute_staleness(entity.id, {"title": "Talk"})
        item.refresh_from_db()
        self.assertFalse(item.is_stale)

        # Bound field changes -> resolved payload changes -> stale.
        recompute_staleness(entity.id, {"title": "New Talk"})
        item.refresh_from_db()
        self.assertTrue(item.is_stale)

        # Unrelated effective keys (not bound) must not cause false staleness.
        item.is_stale = False
        item.save(update_fields=["is_stale"])
        recompute_staleness(entity.id, {"title": "Talk", "unrelated": "changed-but-not-bound"})
        item.refresh_from_db()
        self.assertFalse(item.is_stale)


class ResolveSubmodelSlotsTests(TestCase):
    """events-and-sync.md §13.3: `resolve_submodel_slots` enumerates a
    submodel_list field's children for VEVENT fan-out."""

    databases = ["default"]

    def setUp(self):
        from userdefinedmodel.models import ConfigLanguage, ConfigVersion, DataField, FieldConfig
        from userdefinedmodel.tests.factories import UserDefinedModelEntityFactory, UserDefinedModelTypeFactory

        self.sub_config = FieldConfig.objects.create(name="Timeslot Sub Config")
        ConfigLanguage.objects.create(config=self.sub_config, code="en", label="English", is_default=True)
        self.sub_version = ConfigVersion.objects.create(config=self.sub_config, status="published")
        self.start_field = DataField.objects.create(version=self.sub_version, slug="start", data_type="datetime")
        self.end_field = DataField.objects.create(version=self.sub_version, slug="end", data_type="datetime")

        self.config = FieldConfig.objects.create(name="Event-like Root Config")
        ConfigLanguage.objects.create(config=self.config, code="en", label="English", is_default=True)
        self.version = ConfigVersion.objects.create(config=self.config, status="published")
        self.timeslots_field = DataField.objects.create(
            version=self.version, slug="timeslots", data_type="submodel_list",
            submodel_config=self.sub_version,
        )

        self.udm_type = UserDefinedModelTypeFactory(name="Event-like Type", field_config=self.config)
        self.entity = UserDefinedModelEntityFactory(
            config_version=self.version, user_defined_model_type=self.udm_type,
        )

    def _add_slot(self, start, end):
        from userdefinedmodel.models import FieldValue, UserDefinedModelEntityNode

        child = UserDefinedModelEntityNode.objects.create(
            parent_node=self.entity, parent_field=self.timeslots_field, config_version=self.sub_version,
        )
        FieldValue.objects.create(node=child, field=self.start_field, language="", value_datetime=start)
        FieldValue.objects.create(node=child, field=self.end_field, language="", value_datetime=end)
        return child

    def test_resolves_each_child_start_end(self):
        import datetime

        start1 = datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc)
        end1 = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        start2 = datetime.datetime(2026, 6, 16, 9, 0, tzinfo=datetime.timezone.utc)
        end2 = datetime.datetime(2026, 6, 16, 10, 0, tzinfo=datetime.timezone.utc)
        child1 = self._add_slot(start1, end1)
        child2 = self._add_slot(start2, end2)

        spec = SubmodelSpec(submodel="timeslots", start="start", end="end")
        slots = resolve_submodel_slots(spec, entity=self.entity)

        self.assertEqual(slots, [
            {"child_id": str(child1.id), "start": start1.isoformat(), "end": end1.isoformat()},
            {"child_id": str(child2.id), "start": start2.isoformat(), "end": end2.isoformat()},
        ])

    def test_no_children_returns_empty_list(self):
        spec = SubmodelSpec(submodel="timeslots", start="start", end="end")
        self.assertEqual(resolve_submodel_slots(spec, entity=self.entity), [])

    def test_optional_end_omitted(self):
        import datetime

        start = datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc)
        child = self._add_slot(start, start)
        spec = SubmodelSpec(submodel="timeslots", start="start")
        slots = resolve_submodel_slots(spec, entity=self.entity)
        self.assertEqual(slots, [{"child_id": str(child.id), "start": start.isoformat(), "end": None}])

    def test_resolve_deep_recognizes_submodel_spec(self):
        """A dict shaped like SubmodelSpec (nested inside a plugin's tab
        config, e.g. sync_caldav's "submodel" key) resolves to the actual
        slot list via the generic resolve_deep dispatch, same mechanism as
        BindingSource."""
        import datetime

        start = datetime.datetime(2026, 6, 15, 9, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
        child = self._add_slot(start, end)

        value = {"submodel": "timeslots", "start": "start", "end": "end"}
        resolved = resolve_deep(value, entity=self.entity, effective={})
        self.assertEqual(resolved, [{"child_id": str(child.id), "start": start.isoformat(), "end": end.isoformat()}])
