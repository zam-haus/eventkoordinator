"""API tests for /api/udm/mail-templates/."""
from userdefinedmodel.models import MailTemplate
from userdefinedmodel.tests.test_api import BaseAPITest


class MailTemplateApiTests(BaseAPITest):
    def _create(self, **overrides):
        payload = {
            "slug": "welcome",
            "description": "Welcome mail",
            "subject": "Hi {{ name }}",
            "body_text": "Hello {{ name }}",
            "body_html": "<p>Hello {{ name }}</p>",
            "example_input": {"name": "Ada"},
        }
        payload.update(overrides)
        return self.post("/mail-templates/", payload)

    def test_create_and_get(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["slug"], "welcome")

        resp = self.get("/mail-templates/welcome/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["example_input"], {"name": "Ada"})

    def test_list_omits_bodies(self):
        self._create()
        resp = self.get("/mail-templates/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [{"slug": "welcome", "description": "Welcome mail"}])

    def test_update(self):
        self._create()
        resp = self.put("/mail-templates/welcome/", {
            "description": "changed",
            "subject": "S",
            "body_text": "T",
            "body_html": "H",
            "example_input": {},
        })
        self.assertEqual(resp.status_code, 200)
        template = MailTemplate.objects.get(slug="welcome")
        self.assertEqual(template.body_text, "T")
        self.assertEqual(template.description, "changed")

    def test_delete(self):
        self._create()
        self.assertEqual(self.delete("/mail-templates/welcome/").status_code, 204)
        self.assertFalse(MailTemplate.objects.filter(slug="welcome").exists())

    def test_duplicate_slug_is_rejected(self):
        self._create()
        self.assertEqual(self._create().status_code, 400)

    def test_unknown_slug_is_404(self):
        self.assertEqual(self.get("/mail-templates/nope/").status_code, 404)
        self.assertEqual(self.delete("/mail-templates/nope/").status_code, 404)
        self.assertEqual(
            self.put("/mail-templates/nope/", {"body_text": "x"}).status_code, 404
        )

    def test_non_staff_is_denied_on_every_verb(self):
        self._create()
        self.assertEqual(self.get("/mail-templates/", user=self.user).status_code, 403)
        self.assertEqual(self.get("/mail-templates/welcome/", user=self.user).status_code, 403)
        self.assertEqual(self._create(slug="other").status_code, 403)
        self.assertEqual(
            self.put("/mail-templates/welcome/", {"body_text": "x"}).status_code, 403
        )
        self.assertEqual(self.delete("/mail-templates/welcome/").status_code, 403)
        self.assertEqual(
            self.post("/mail-templates/preview/", {"body_text": "x"}).status_code, 403
        )


class MailTemplatePreviewApiTests(BaseAPITest):
    def test_preview_renders(self):
        resp = self.post("/mail-templates/preview/", {
            "subject": "S {{ n }}",
            "body_text": "text {{ n }}",
            "body_html": "<p>{{ n }}</p>",
            "context": {"n": "1"},
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"subject": "S 1", "text": "text 1", "html": "<p>1</p>", "error": None},
        )

    def test_syntax_error_is_200_with_error(self):
        resp = self.post("/mail-templates/preview/", {"body_text": "{% if %}"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["error"])
        self.assertEqual(body["text"], "")

    def test_html_is_never_served_as_html(self):
        resp = self.post("/mail-templates/preview/", {
            "body_html": "<script>alert(1)</script>",
        })
        self.assertEqual(resp["Content-Type"], "application/json; charset=utf-8")
        # The script tag survives as data — it must be sandboxed by the client,
        # not stripped here, since real mail HTML is arbitrary.
        self.assertIn("<script>", resp.json()["html"])

    def test_context_values_are_escaped_in_html_but_not_text(self):
        resp = self.post("/mail-templates/preview/", {
            "body_text": "{{ v }}",
            "body_html": "{{ v }}",
            "context": {"v": "<b>"},
        })
        self.assertEqual(resp.json()["text"], "<b>")
        self.assertEqual(resp.json()["html"], "&lt;b&gt;")
