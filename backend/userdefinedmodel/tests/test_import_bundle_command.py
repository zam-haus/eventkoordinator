"""Tests for the `import_bundle` management command."""
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from userdefinedmodel.models import MailTemplate

#: The bundle declares no scope of its own, so the command needs one passed in.
#: No UDM type with this ID exists; import simply finds nothing to relink.
SCOPE_ID = "00000000-0000-4000-8000-000000000001"


def _write_config_dir(root: Path, *, template_body: str = "Hi {{ name }}") -> Path:
    """A minimal configuration directory: no types, one policy, one template."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "UDM_BUNDLE.json").write_text(json.dumps({
        "version": 1,
        "scope_type_ids": [],
        "udm_types": [],
        "field_configs": [],
        "workflows": [],
        "policies": [{"slug": "demo-policy"}],
        "mail_templates": [],
    }))
    (root / "policies").mkdir(exist_ok=True)
    (root / "policies" / "demo-policy.rego").write_text("package udm\n")
    (root / "templates").mkdir(exist_ok=True)
    (root / "templates" / "demo-mail.txt.j2").write_text(template_body)
    (root / "templates" / "demo-mail.html.j2").write_text("<p>%s</p>" % template_body)
    (root / "templates" / "demo-mail.json").write_text(json.dumps({
        "description": "Demo",
        "subject": "S",
        "example_input": {"name": "Ada"},
    }))
    return root


class ImportBundleCommandTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = _write_config_dir(Path(self.tmp.name) / "configuration")

    def _run(self, *args):
        out = StringIO()
        call_command("import_bundle", "--dir", str(self.dir), *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_imports_policies_and_templates(self):
        from userdefinedmodel.models import Policy

        output = self._run("--scope-type-ids", SCOPE_ID)
        self.assertIn("1 mail templates", output)
        self.assertTrue(Policy.objects.filter(slug="demo-policy").exists())
        template = MailTemplate.objects.get(slug="demo-mail")
        self.assertEqual(template.body_text, "Hi {{ name }}")
        self.assertEqual(template.body_html, "<p>Hi {{ name }}</p>")
        self.assertEqual(template.subject, "S")
        self.assertEqual(template.example_input, {"name": "Ada"})

    def test_reimport_updates_in_place(self):
        self._run("--scope-type-ids", SCOPE_ID)
        (self.dir / "templates" / "demo-mail.txt.j2").write_text("Changed")
        self._run("--scope-type-ids", SCOPE_ID)
        self.assertEqual(MailTemplate.objects.get(slug="demo-mail").body_text, "Changed")
        self.assertEqual(MailTemplate.objects.filter(slug="demo-mail").count(), 1)

    def test_broken_template_fails_before_writing(self):
        (self.dir / "templates" / "demo-mail.txt.j2").write_text("{% if %}")
        with self.assertRaises(CommandError):
            self._run("--scope-type-ids", SCOPE_ID)
        self.assertFalse(MailTemplate.objects.exists())

    def test_dry_run_writes_nothing(self):
        self._run("--scope-type-ids", SCOPE_ID, "--dry-run")
        self.assertFalse(MailTemplate.objects.exists())

    def test_missing_directory_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command("import_bundle", "--dir", str(self.dir / "nope"))

    def test_empty_scope_is_rejected(self):
        with self.assertRaises(CommandError):
            self._run()
