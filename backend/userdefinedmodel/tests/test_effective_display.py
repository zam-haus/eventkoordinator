"""Tests for the effective-values output convention, markdown display
rendering, and the entity_url filter (events-and-sync.md §1.3/1.4/1.6,
Step 4)."""
from django.test import TestCase, override_settings

from project.jinja_filters import entity_url
from userdefinedmodel.display_templates import render_markdown_display, render_markdown_displays_for_entity
from userdefinedmodel.engine import evaluate_policy
from userdefinedmodel.tests.factories import (
    UserFactory, make_entity_with_type, wrap_policy,
)
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE, _make_field_with_label

EFFECTIVE_POLICY = wrap_policy("""
package udm
import rego.v1

allow := true

effective["title"] := v if {
    v := input.entity.fields.title_override.value
    v != null
}
effective["title"] := "fallback title" if {
    input.entity.fields.title_override.value == null
}
""")


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class EffectiveValuesTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.user = UserFactory()

    def test_effective_coalesce_with_override(self):
        entity, _, version, _ = make_entity_with_type(policy_source=EFFECTIVE_POLICY)
        _make_field_with_label(version, "title_override", "text_short", label="Title override")
        from userdefinedmodel.tests.factories import FieldValueFactory
        field = version.field_definitions.get(slug="title_override")
        FieldValueFactory(node=entity, field=field, value_text="Overridden")

        output = evaluate_policy(entity, self.user, "view")
        self.assertEqual(output.effective.get("title"), "Overridden")

    def test_effective_coalesce_fallback(self):
        entity, _, version, _ = make_entity_with_type(policy_source=EFFECTIVE_POLICY)
        _make_field_with_label(version, "title_override", "text_short", label="Title override")

        output = evaluate_policy(entity, self.user, "view")
        self.assertEqual(output.effective.get("title"), "fallback title")


class MarkdownDisplayTests(TestCase):
    def test_renders_effective_and_entity_context(self):
        rendered = render_markdown_display(
            "# {{ effective.title }}\n\nid={{ entity.id }}",
            effective={"title": "My Title"},
            entity={"id": "abc-123"},
            linked={},
            backlinks={},
        )
        self.assertIn("# My Title", rendered)
        self.assertIn("id=abc-123", rendered)

    def test_template_error_does_not_raise(self):
        rendered = render_markdown_display(
            "{{ effective.title.nonexistent_call() }}",
            effective={"title": "x"}, entity={}, linked={}, backlinks={},
        )
        self.assertIn("template error", rendered)

    def test_empty_template_renders_empty(self):
        self.assertEqual(render_markdown_display("", effective={}, entity={}, linked={}, backlinks={}), "")


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class MarkdownDisplayIntegrationTests(TestCase):
    databases = ["default"]

    def test_render_markdown_displays_for_entity(self):
        entity, _, version, _ = make_entity_with_type(policy_source=EFFECTIVE_POLICY)
        _make_field_with_label(version, "title_override", "text_short", label="Title override")
        from userdefinedmodel.models import FormElement
        FormElement.objects.create(
            version=version, slug="summary", element_type=FormElement.ElementType.MARKDOWN_DISPLAY,
            sort_order=0, is_preview=False,
            type_config={"template": "Effective title: {{ effective.title }}"},
        )
        user = UserFactory()
        output = evaluate_policy(entity, user, "view")

        rendered = render_markdown_displays_for_entity(version, output)
        self.assertEqual(rendered.get("summary"), "Effective title: fallback title")

    def test_no_markdown_elements_returns_empty(self):
        entity, _, version, _ = make_entity_with_type()
        user = UserFactory()
        output = evaluate_policy(entity, user, "view")
        self.assertEqual(render_markdown_displays_for_entity(version, output), {})


class EntityUrlFilterTests(TestCase):
    @override_settings(FRONTEND_BASE_URL="https://example.org")
    def test_builds_frontend_url(self):
        self.assertEqual(entity_url("abc-123"), "https://example.org/udm-entity/abc-123")

    @override_settings(FRONTEND_BASE_URL="https://example.org/")
    def test_strips_trailing_slash(self):
        self.assertEqual(entity_url("abc-123"), "https://example.org/udm-entity/abc-123")

    def test_none_yields_empty_string(self):
        self.assertEqual(entity_url(None), "")
