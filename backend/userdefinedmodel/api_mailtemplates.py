"""Mail template routes: /mail-templates/...

Note the rendered HTML is only ever returned as a JSON string field. No endpoint
here serves it as text/html — doing so would turn a staff-editable template into
a same-origin stored-XSS sink. The frontend renders it in a sandboxed iframe.
"""
from __future__ import annotations

from django.db import IntegrityError
from django.http import HttpResponse, JsonResponse
from ninja import Router
from ninja.security import django_auth

from userdefinedmodel.api_helpers import _require_perms
from userdefinedmodel.mailtemplates import render_source
from userdefinedmodel.schemas import (
    MailTemplateCreateIn,
    MailTemplateOut,
    MailTemplatePreviewIn,
    MailTemplatePreviewOut,
    MailTemplateSummaryOut,
    MailTemplateUpdateIn,
)

router = Router(auth=django_auth)


def _out(t) -> MailTemplateOut:
    return MailTemplateOut(
        slug=t.slug,
        description=t.description,
        subject=t.subject,
        body_text=t.body_text,
        body_html=t.body_html,
        example_input=t.example_input or {},
    )


@router.get("/mail-templates/", response=list[MailTemplateSummaryOut], auth=django_auth)
def list_mail_templates(request):
    from userdefinedmodel.models import MailTemplate
    if denied := _require_perms(request, "userdefinedmodel.view_mailtemplate"):
        return denied
    return [
        MailTemplateSummaryOut(slug=t.slug, description=t.description)
        for t in MailTemplate.objects.all()
    ]


@router.post("/mail-templates/", response={201: MailTemplateOut}, auth=django_auth)
def create_mail_template(request, payload: MailTemplateCreateIn):
    from userdefinedmodel.models import MailTemplate
    if denied := _require_perms(request, "userdefinedmodel.add_mailtemplate"):
        return denied
    try:
        template = MailTemplate.objects.create(**payload.dict())
    except IntegrityError:
        return JsonResponse({"detail": f"Template '{payload.slug}' already exists"}, status=400)
    return 201, _out(template)


@router.post("/mail-templates/preview/", response=MailTemplatePreviewOut, auth=django_auth)
def preview_mail_template(request, payload: MailTemplatePreviewIn):
    """Render unsaved sources against a sample context.

    Declared before the ``{slug}`` routes so it is not swallowed by them.

    A template syntax error is normal editing state, not an API failure, so it
    comes back as HTTP 200 with ``error`` set rather than a 4xx/5xx.
    """
    if denied := _require_perms(request, "userdefinedmodel.view_mailtemplate"):
        return denied
    try:
        rendered = render_source(
            payload.body_text, payload.body_html, payload.context, payload.subject
        )
    except Exception as exc:
        return MailTemplatePreviewOut(error=f"{type(exc).__name__}: {exc}")
    return MailTemplatePreviewOut(
        subject=rendered.subject, text=rendered.text, html=rendered.html
    )


@router.get("/mail-templates/{slug}/", response=MailTemplateOut, auth=django_auth)
def get_mail_template(request, slug: str):
    from userdefinedmodel.models import MailTemplate
    if denied := _require_perms(request, "userdefinedmodel.view_mailtemplate"):
        return denied
    try:
        template = MailTemplate.objects.get(slug=slug)
    except MailTemplate.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return _out(template)


@router.put("/mail-templates/{slug}/", response=MailTemplateOut, auth=django_auth)
def update_mail_template(request, slug: str, payload: MailTemplateUpdateIn):
    from userdefinedmodel.models import MailTemplate
    if denied := _require_perms(request, "userdefinedmodel.change_mailtemplate"):
        return denied
    try:
        template = MailTemplate.objects.get(slug=slug)
    except MailTemplate.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    for field, value in payload.dict().items():
        setattr(template, field, value)
    template.save()
    return _out(template)


@router.delete("/mail-templates/{slug}/", auth=django_auth)
def delete_mail_template(request, slug: str):
    from userdefinedmodel.models import MailTemplate
    if denied := _require_perms(request, "userdefinedmodel.delete_mailtemplate"):
        return denied
    try:
        template = MailTemplate.objects.get(slug=slug)
    except MailTemplate.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    template.delete()
    return HttpResponse(status=204)
