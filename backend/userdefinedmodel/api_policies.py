"""Top-level policy routes: /policies/..."""
from __future__ import annotations

from django.http import JsonResponse
from ninja import Router
from ninja.security import django_auth

from userdefinedmodel.api_helpers import _require_perms
from userdefinedmodel.schemas import PolicyCreateIn, PolicyOut, PolicyUpdateIn

router = Router(auth=django_auth)


@router.get("/policies/", response=list[PolicyOut], auth=django_auth)
def list_policies(request):
    from userdefinedmodel.models import Policy
    if denied := _require_perms(request, "userdefinedmodel.view_policy"):
        return denied
    return [PolicyOut(slug=p.slug, source=p.source) for p in Policy.objects.all()]


@router.post("/policies/", response={201: PolicyOut}, auth=django_auth)
def create_policy(request, payload: PolicyCreateIn):
    from userdefinedmodel.models import Policy
    if denied := _require_perms(request, "userdefinedmodel.add_policy"):
        return denied
    policy = Policy.objects.create(slug=payload.slug, source=payload.source)
    return 201, PolicyOut(slug=policy.slug, source=policy.source)


@router.get("/policies/{slug}/", response=PolicyOut, auth=django_auth)
def get_policy(request, slug: str):
    from userdefinedmodel.models import Policy
    if denied := _require_perms(request, "userdefinedmodel.view_policy"):
        return denied
    try:
        p = Policy.objects.get(slug=slug)
    except Policy.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return PolicyOut(slug=p.slug, source=p.source)


@router.put("/policies/{slug}/", response=PolicyOut, auth=django_auth)
def update_policy(request, slug: str, payload: PolicyUpdateIn):
    from userdefinedmodel.models import Policy
    if denied := _require_perms(request, "userdefinedmodel.change_policy"):
        return denied
    try:
        p = Policy.objects.get(slug=slug)
    except Policy.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    p.source = payload.source
    p.save()
    return PolicyOut(slug=p.slug, source=p.source)


@router.delete("/policies/{slug}/", auth=django_auth)
def delete_policy(request, slug: str):
    from userdefinedmodel.models import Policy
    if denied := _require_perms(request, "userdefinedmodel.delete_policy"):
        return denied
    try:
        p = Policy.objects.get(slug=slug)
    except Policy.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if p.type_assignments.exists():
        return JsonResponse({"detail": "Policy is assigned to UDMTypes"}, status=400)
    p.delete()
    return JsonResponse({}, status=204)
