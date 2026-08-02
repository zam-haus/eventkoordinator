"""Autocomplete routes: /users/, /groups/, /entity-search/"""
from __future__ import annotations

import uuid
from typing import Optional

from ninja import Query, Router
from ninja.security import django_auth

from userdefinedmodel.api_helpers import _locale
from userdefinedmodel.schemas import EntityAutocompleteItem, GroupAutocompleteItem, UserAutocompleteItem

router = Router(auth=django_auth)


@router.get("/users/", response=list[UserAutocompleteItem], auth=django_auth)
def search_users(
    request,
    q: str = "",
    group_ids: Optional[list[int]] = Query(None),
    ids: Optional[list[uuid.UUID]] = Query(None),
):
    from openid_user_management.models import OpenIDUser
    from django.db.models import Q as DQ
    qs = OpenIDUser.objects.filter(is_active=True)
    if group_ids:
        qs = qs.filter(groups__id__in=group_ids).distinct()
    if q:
        qs = qs.filter(DQ(username__icontains=q) | DQ(email__icontains=q))
    if ids:
        qs = qs.filter(id__in=ids)
    return [UserAutocompleteItem(id=u.id, display_name=u.username) for u in qs[:50]]


@router.get("/groups/", response=list[GroupAutocompleteItem], auth=django_auth)
def search_groups(
    request,
    q: str = "",
    ids: Optional[list[int]] = Query(None),
):
    from django.contrib.auth.models import Group
    qs = Group.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    if ids:
        qs = qs.filter(id__in=ids)
    return [GroupAutocompleteItem(id=g.id, name=g.name) for g in qs[:50]]


@router.get("/entity-search/", response=list[EntityAutocompleteItem], auth=django_auth)
def search_entities(
    request,
    q: str = "",
    type_ids: Optional[list[uuid.UUID]] = Query(None),
    ids: Optional[list[uuid.UUID]] = Query(None),
):
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.engine import evaluate_policy
    _entity_prefetch = [
        "field_values__field",
        "config_version__field_definitions",
        "config_version__config__languages",
        "user_defined_model_type__type_policies__policy",
    ]
    qs = UserDefinedModelEntity.objects.select_related(
        "config_version__config", "user_defined_model_type"
    ).prefetch_related(*_entity_prefetch)
    if type_ids:
        qs = qs.filter(user_defined_model_type_id__in=type_ids)
    if ids:
        qs = qs.filter(id__in=ids)
    # Object-level filter: only surface entities the user may browse/view. We
    # scan past non-viewable rows rather than slicing first, so the result can
    # still reach the cap of 50 visible entities. Superusers also get entities
    # they may not browse — GUID only, no preview content — so they can select
    # them in the policy evaluator.
    results = []
    for entity in qs.iterator(chunk_size=200):
        if not evaluate_policy(entity, request.user, "browse", locale=_locale(request)).allow:
            if not request.user.is_superuser:
                continue
            results.append(EntityAutocompleteItem(
                id=entity.id,
                display=str(entity.id),
                type_id=entity.user_defined_model_type_id,
            ))
        else:
            # §5-4: the display string is built from is_preview fields, which
            # are entity data — gate them on the VIEW policy's per-node grant,
            # not just on browse.allow.
            view_policy = evaluate_policy(entity, request.user, "view", locale=_locale(request))
            viewable_slugs = set(view_policy.viewable_fields.get(str(entity.id), [])) if view_policy.allow else set()
            results.append(EntityAutocompleteItem(
                id=entity.id,
                display=_entity_preview_display(entity, viewable_slugs),
                type_id=entity.user_defined_model_type_id,
            ))
        if len(results) >= 50:
            break
    return results


def _entity_preview_display(entity, viewable_slugs: set[str]) -> str:
    """Build a human-readable display string from is_preview fields that the
    VIEW policy exposes to this user, falling back to the UUID."""
    config_version = entity.config_version
    if config_version is None:
        return str(entity.id)

    # Determine default language code for localized fields
    default_lang = ""
    for lang in config_version.config.languages.all():
        if lang.is_default:
            default_lang = lang.code
            break

    # is_preview now lives on FormElement (not DataField). Resolve the set of
    # data-field slugs whose bound FormElement is marked is_preview.
    preview_slugs = set(
        config_version.form_elements.filter(
            is_preview=True, element_type="field"
        ).values_list("bindings__data_field__slug", flat=True)
    )
    preview_fields = [
        fd for fd in config_version.field_definitions.all()
        if fd.slug in preview_slugs and fd.slug in viewable_slugs
    ]
    if not preview_fields:
        return str(entity.id)

    # Build slug → field_values map from the prefetched relation
    fv_map: dict[tuple, object] = {}
    for fv in entity.field_values.all():
        fv_map[(fv.field_id, fv.language)] = fv

    parts = []
    for fd in preview_fields:
        if fd.is_localized:
            fv = fv_map.get((fd.id, default_lang)) or next(
                (fv_map[k] for k in fv_map if k[0] == fd.id), None
            )
        else:
            fv = fv_map.get((fd.id, ""))

        if fv is None:
            continue
        val = fv.get_value(field=fd)
        if val is not None and val != "":
            if fd.data_type == "slug_id":
                prefix = (fd.type_config or {}).get("prefix", "")
                parts.append(f"{prefix}-{int(val)}" if prefix else str(val))
            else:
                parts.append(str(val))

    return " · ".join(parts) if parts else str(entity.id)
