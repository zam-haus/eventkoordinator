"""Autocomplete routes: /users/, /groups/, /entity-search/"""
from __future__ import annotations

from ninja import Router
from ninja.security import django_auth

from userdefinedmodel.api_helpers import _locale
from userdefinedmodel.schemas import EntityAutocompleteItem, GroupAutocompleteItem, UserAutocompleteItem

router = Router(auth=django_auth)


@router.get("/users/", response=list[UserAutocompleteItem], auth=django_auth)
def search_users(request, q: str = "", group_ids: str = "", ids: str = ""):
    from openid_user_management.models import OpenIDUser
    from django.db.models import Q as DQ
    qs = OpenIDUser.objects.filter(is_active=True)
    if group_ids:
        gids = [int(x) for x in group_ids.split(",") if x.strip().isdigit()]
        qs = qs.filter(groups__id__in=gids)
    if q:
        qs = qs.filter(DQ(username__icontains=q) | DQ(email__icontains=q))
    if ids:
        uid_list = [x.strip() for x in ids.split(",") if x.strip()]
        qs = OpenIDUser.objects.filter(id__in=uid_list)
    return [UserAutocompleteItem(id=u.id, display_name=u.username) for u in qs[:50]]


@router.get("/groups/", response=list[GroupAutocompleteItem], auth=django_auth)
def search_groups(request, q: str = "", ids: str = ""):
    from django.contrib.auth.models import Group
    qs = Group.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        qs = Group.objects.filter(id__in=id_list)
    return [GroupAutocompleteItem(id=g.id, name=g.name) for g in qs[:50]]


@router.get("/entity-search/", response=list[EntityAutocompleteItem], auth=django_auth)
def search_entities(request, q: str = "", type_ids: str = "", ids: str = ""):
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
        tid_list = [x.strip() for x in type_ids.split(",") if x.strip()]
        qs = qs.filter(user_defined_model_type_id__in=tid_list)
    if ids:
        id_list = [x.strip() for x in ids.split(",") if x.strip()]
        qs = UserDefinedModelEntity.objects.select_related(
            "config_version__config", "user_defined_model_type"
        ).filter(id__in=id_list).prefetch_related(*_entity_prefetch)
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

    preview_fields = [
        fd for fd in config_version.field_definitions.all()
        if fd.is_preview and fd.slug in viewable_slugs
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
