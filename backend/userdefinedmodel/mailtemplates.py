"""Rendering of DB-stored mail templates (:class:`MailTemplate`).

Template sources are editable by staff users, so they are rendered in a
``SandboxedEnvironment`` with a curated set of globals. In particular the Django
``settings`` object is **not** exposed here (unlike ``project.jinja2``), because
it would let anyone with ``change_mailtemplate`` read every secret.

Contexts are round-tripped through JSON before rendering, so templates only ever
see plain data and can never traverse ORM relations.
"""

import json
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone as django_timezone
from jinja2 import ChainableUndefined
from jinja2.sandbox import SandboxedEnvironment

from project.jinja_filters import SAFE_FILTERS

logger = logging.getLogger(__name__)


class MailTemplateNotFound(ValueError):
    """No MailTemplate exists for the requested slug."""


class MailTemplateRenderError(ValueError):
    """The template source failed to compile or render."""


@dataclass(frozen=True)
class RenderedMail:
    subject: str
    text: str
    html: str


_ENVS: dict[bool, SandboxedEnvironment] = {}


def _globals() -> dict:
    return {
        "frontend_base_url": settings.FRONTEND_BASE_URL,
        "site_name": getattr(settings, "SITE_NAME", ""),
        "default_from_email": getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        "now": django_timezone.now,
    }


def get_environment(autoescape: bool) -> SandboxedEnvironment:
    """Return the sandboxed environment for HTML (autoescape) or plaintext."""
    env = _ENVS.get(autoescape)
    if env is None:
        env = SandboxedEnvironment(
            autoescape=autoescape,
            undefined=ChainableUndefined,
            keep_trailing_newline=True,
        )
        env.filters.update(SAFE_FILTERS)
        _ENVS[autoescape] = env
    # Globals depend on settings, which tests override; refresh on every call.
    env.globals.update(_globals())
    return env


def jsonify_context(context: dict | None) -> dict:
    """Reduce a context to plain JSON data.

    Anything not JSON-serialisable (model instances, datetimes) becomes its
    ``str()``, so a template author cannot reach through an object passed by a
    careless caller.
    """
    return json.loads(json.dumps(context or {}, default=str))


def render_string(source: str, context: dict, *, autoescape: bool) -> str:
    if not source:
        return ""
    return get_environment(autoescape).from_string(source).render(**context)


def render_source(
    body_text: str,
    body_html: str,
    context: dict | None = None,
    subject: str = "",
) -> RenderedMail:
    """Render unsaved template sources. Used by the editor preview."""
    ctx = jsonify_context(context)
    return RenderedMail(
        subject=render_string(subject, ctx, autoescape=False).strip(),
        text=render_string(body_text, ctx, autoescape=False),
        html=render_string(body_html, ctx, autoescape=True),
    )


def get_template(slug: str):
    from userdefinedmodel.models import MailTemplate

    try:
        return MailTemplate.objects.get(slug=slug)
    except MailTemplate.DoesNotExist:
        raise MailTemplateNotFound(f"No mail template with slug '{slug}'")


def render_mail_template(slug: str, context: dict | None = None) -> RenderedMail:
    template = get_template(slug)
    return render_source(template.body_text, template.body_html, context, template.subject)


def send_mail_template(
    slug: str,
    context: dict | None = None,
    *,
    recipient_list: list[str],
    subject: str | None = None,
    fail_silently: bool = False,
) -> RenderedMail:
    """Render ``slug`` and send it. ``subject`` overrides the template's own."""
    recipients = [r for r in recipient_list if r]
    rendered = render_mail_template(slug, context)
    effective_subject = subject if subject is not None else rendered.subject
    if not recipients:
        logger.warning("Mail template '%s' rendered but has no recipients", slug)
        return rendered
    send_mail(
        subject=effective_subject,
        message=rendered.text,
        html_message=rendered.html or None,
        from_email=None,
        recipient_list=recipients,
        fail_silently=fail_silently,
    )
    return rendered
