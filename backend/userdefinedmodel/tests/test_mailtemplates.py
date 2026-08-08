"""
Tests for the shared Jinja filters (project/jinja_filters.py) and the sandboxed
mail-template renderer (userdefinedmodel/mailtemplates.py).
"""
from __future__ import annotations

import datetime

from django.test import TestCase, override_settings
from jinja2.exceptions import SecurityError, UndefinedError

from project.jinja_filters import isoformat, htmlquote, tz_convert, userinput
from userdefinedmodel.mailtemplates import (
    MailTemplateNotFound,
    get_environment,
    RenderedMail,
    render_mail_template,
    render_source,
    render_string,
    send_mail_template,
)
from userdefinedmodel.models import MailTemplate

BERLIN = "Europe/Berlin"


class TimezoneFilterTests(TestCase):
    def test_aware_datetime_is_converted(self):
        value = datetime.datetime(2026, 8, 8, 10, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(tz_convert(value, BERLIN).hour, 12)

    def test_naive_datetime_is_interpreted_in_project_timezone(self):
        # settings.TIME_ZONE is UTC, so 10:00 naive == 12:00 Berlin in summer.
        value = datetime.datetime(2026, 8, 8, 10, 0)
        self.assertEqual(tz_convert(value, BERLIN).hour, 12)

    def test_winter_time_uses_plus_one(self):
        value = datetime.datetime(2026, 1, 8, 10, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(tz_convert(value, BERLIN).hour, 11)

    def test_dst_boundary(self):
        # 2026-03-29 01:00 UTC is the moment Berlin jumps from +01:00 to +02:00.
        before = datetime.datetime(2026, 3, 29, 0, 30, tzinfo=datetime.timezone.utc)
        after = datetime.datetime(2026, 3, 29, 1, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(tz_convert(before, BERLIN).utcoffset(), datetime.timedelta(hours=1))
        self.assertEqual(tz_convert(after, BERLIN).utcoffset(), datetime.timedelta(hours=2))
        self.assertEqual(tz_convert(after, BERLIN).hour, 3)

    def test_iso_string_with_z(self):
        self.assertEqual(tz_convert("2026-08-08T10:00:00Z", BERLIN).hour, 12)

    def test_iso_string_with_offset(self):
        self.assertEqual(tz_convert("2026-08-08T10:00:00+00:00", BERLIN).hour, 12)

    def test_date_passes_through(self):
        value = datetime.date(2026, 8, 8)
        self.assertEqual(tz_convert(value, BERLIN), value)

    def test_timestamp(self):
        self.assertEqual(tz_convert(1786000000, BERLIN).tzinfo.key, BERLIN)

    def test_none_and_empty(self):
        self.assertIsNone(tz_convert(None))
        self.assertIsNone(tz_convert("   "))

    def test_invalid_string_raises(self):
        with self.assertRaises(ValueError):
            tz_convert("not a date")

    def test_unknown_timezone_raises(self):
        with self.assertRaises(Exception):
            tz_convert("2026-08-08T10:00:00Z", "Mars/Olympus")


class IsoformatFilterTests(TestCase):
    def test_defaults_drop_microseconds_and_use_space(self):
        value = datetime.datetime(2026, 8, 8, 12, 0, 0, 123456, tzinfo=datetime.timezone.utc)
        self.assertEqual(isoformat(value), "2026-08-08 12:00:00+00:00")

    def test_strict_separator(self):
        value = datetime.datetime(2026, 8, 8, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(isoformat(value, sep="T"), "2026-08-08T12:00:00+00:00")

    def test_date(self):
        self.assertEqual(isoformat(datetime.date(2026, 8, 8)), "2026-08-08")

    def test_none_is_empty(self):
        self.assertEqual(isoformat(None), "")

    def test_string_passes_through(self):
        self.assertEqual(isoformat("already formatted"), "already formatted")


class FilterCompositionTests(TestCase):
    """The syntax mails are required to use: | timezone(...) | isoformat()."""

    def _render(self, value):
        return render_string(
            '{{ v | timezone("Europe/Berlin") | isoformat() }}',
            {"v": value},
            autoescape=False,
        )

    def test_aware_datetime(self):
        value = datetime.datetime(2026, 8, 8, 10, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(self._render(value.isoformat()), "2026-08-08 12:00:00+02:00")

    def test_none_renders_empty(self):
        self.assertEqual(self._render(None), "")

    def test_missing_key_renders_empty(self):
        out = render_string(
            '{{ nope.deeply.missing | timezone("Europe/Berlin") | isoformat() }}',
            {},
            autoescape=False,
        )
        self.assertEqual(out, "")


class UserinputFilterTests(TestCase):
    def test_every_line_is_indented(self):
        self.assertEqual(userinput("a\nb"), "    a\n    b")

    def test_blank_lines_carry_no_trailing_whitespace(self):
        self.assertEqual(userinput("a\n\nb"), "    a\n\n    b")

    def test_crlf_is_normalised(self):
        self.assertEqual(userinput("a\r\nb"), "    a\n    b")

    def test_single_trailing_newline_is_dropped(self):
        self.assertEqual(userinput("a\n"), "    a")

    def test_empty_uses_placeholder(self):
        self.assertEqual(userinput(""), "    (leer / empty)")
        self.assertEqual(userinput(None), "    (leer / empty)")
        self.assertEqual(userinput("   \n  "), "    (leer / empty)")

    def test_custom_prefix(self):
        self.assertEqual(userinput("a\nb", "> "), "> a\n> b")

    def test_non_string_input(self):
        self.assertEqual(userinput(42), "    42")


class HtmlquoteFilterTests(TestCase):
    def test_escapes_and_breaks_lines(self):
        self.assertEqual(str(htmlquote("<b>\nx")), "&lt;b&gt;<br>\nx")

    def test_is_not_double_escaped_in_html_env(self):
        out = render_string("{{ v | htmlquote }}", {"v": "<b>"}, autoescape=True)
        self.assertEqual(out, "&lt;b&gt;")


class SandboxTests(TestCase):
    def test_attribute_escape_is_blocked(self):
        # The sandbox marks dunder attributes unsafe, so the classic
        # ''.__class__.__mro__ escape chain yields undefined instead of the
        # type object. A plain Environment would happily print it.
        from jinja2 import Environment

        self.assertEqual(render_string("{{ ''.__class__.__mro__ }}", {}, autoescape=False), "")
        self.assertNotEqual(Environment().from_string("{{ ''.__class__ }}").render(), "")

    def test_calling_a_blocked_attribute_raises(self):
        with self.assertRaises(UndefinedError):
            render_string("{{ [].append.__globals__() }}", {}, autoescape=False)

    def test_unsafe_callable_is_refused(self):
        class Evil:
            def wipe(self):  # pragma: no cover - must never be called
                raise AssertionError("called")

            wipe.unsafe_callable = True

        with self.assertRaises(SecurityError):
            get_environment(False).from_string("{{ v.wipe() }}").render(v=Evil())

    def test_settings_is_not_exposed(self):
        self.assertEqual(render_string("{{ settings.SECRET_KEY }}", {}, autoescape=False), "")

    @override_settings(FRONTEND_BASE_URL="https://example.test")
    def test_frontend_base_url_global(self):
        self.assertEqual(
            render_string("{{ frontend_base_url }}", {}, autoescape=False),
            "https://example.test",
        )

    def test_autoescape_is_per_environment(self):
        self.assertEqual(render_string("{{ v }}", {"v": "<b>"}, autoescape=False), "<b>")
        self.assertEqual(render_string("{{ v }}", {"v": "<b>"}, autoescape=True), "&lt;b&gt;")


class RenderSourceTests(TestCase):
    def test_renders_all_three_parts(self):
        result = render_source("text {{ v }}", "<p>{{ v }}</p>", {"v": "x"}, subject="s {{ v }}")
        self.assertEqual(result, RenderedMail(subject="s x", text="text x", html="<p>x</p>"))

    def test_context_is_reduced_to_json(self):
        # A model instance must become its str(), not remain traversable.
        tpl = MailTemplate.objects.create(slug="t")
        out = render_source("{{ v }}|{{ v.body_text }}", "", {"v": tpl})
        self.assertEqual(out.text, "t|")


class MailTemplateRenderingTests(TestCase):
    def test_unknown_slug_raises(self):
        with self.assertRaises(MailTemplateNotFound):
            render_mail_template("does-not-exist")

    def test_render_and_send(self):
        MailTemplate.objects.create(
            slug="hello",
            subject="Hi {{ name }}",
            body_text="Hello {{ name }}",
            body_html="<p>Hello {{ name }}</p>",
        )
        from django.core import mail

        send_mail_template("hello", {"name": "Ada"}, recipient_list=["a@example.org"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Hi Ada")
        self.assertEqual(mail.outbox[0].body, "Hello Ada")
        self.assertEqual(mail.outbox[0].alternatives[0][1], "text/html")

    def test_subject_override(self):
        MailTemplate.objects.create(slug="hello", subject="from template", body_text="x")
        from django.core import mail

        send_mail_template("hello", {}, recipient_list=["a@example.org"], subject="explicit")
        self.assertEqual(mail.outbox[0].subject, "explicit")

    def test_no_recipients_does_not_send(self):
        MailTemplate.objects.create(slug="hello", body_text="x")
        from django.core import mail

        send_mail_template("hello", {}, recipient_list=[None, ""])
        self.assertEqual(len(mail.outbox), 0)
