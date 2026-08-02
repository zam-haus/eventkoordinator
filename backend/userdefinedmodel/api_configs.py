"""FieldConfig / ConfigVersion routes: /configs/..., /config-versions/..."""
from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from ninja import Router
from ninja.security import django_auth

from django.http import HttpResponse, JsonResponse

from userdefinedmodel.api_helpers import (
    ApiError,
    _create_field_default,
    _require_perms,
    _serialize_config_version,
    _serialize_version_as_draft_in,
)
from userdefinedmodel.schemas import (
    ConfigDraftExportOut,
    ConfigDraftIn,
    ConfigLanguageOut,
    ConfigVersionOut,
    FieldConfigCreateIn,
    FieldConfigOut,
    FieldConfigUpdateIn,
)

router = Router(auth=django_auth)


# ─── FieldConfig CRUD ─────────────────────────────────────────────────────────

def _field_config_out(cfg) -> FieldConfigOut:
    from userdefinedmodel.models import UserDefinedModelEntityNode, ConfigVersion, FieldDefinition
    published = (
        ConfigVersion.objects.filter(config=cfg, status=ConfigVersion.Status.PUBLISHED)
        .order_by("-published_at")
        .first()
    )
    published_id = published.id if published else None
    base_qs = UserDefinedModelEntityNode.objects.filter(config_version__config=cfg)
    entity_count = base_qs.count()
    stale_count = base_qs.exclude(config_version_id=published_id).count() if published_id is not None else entity_count
    published_submodel_usage_count = (
        FieldDefinition.objects.filter(
            version__status=ConfigVersion.Status.PUBLISHED,
            submodel_config__config=cfg,
        )
        .values("version__config_id")
        .distinct()
        .count()
    )
    version_count = cfg.versions.count()
    return FieldConfigOut(
        id=cfg.id, name=cfg.name, description=cfg.description,
        created_at=cfg.created_at,
        last_published_at=published.published_at if published else None,
        version_count=version_count,
        stale_entity_count=stale_count,
        entity_count=entity_count,
        published_submodel_usage_count=published_submodel_usage_count,
        type_ids=[t.id for t in cfg.user_defined_model_types.all()],
        languages=[
            ConfigLanguageOut(code=l.code, label=l.label, is_default=l.is_default, sort_order=l.sort_order)
            for l in cfg.languages.all()
        ],
    )


@router.get("/configs/", response=list[FieldConfigOut], auth=django_auth)
def list_configs(request):
    from userdefinedmodel.models import FieldConfig
    configs = FieldConfig.objects.prefetch_related("languages", "user_defined_model_types")
    return [_field_config_out(cfg) for cfg in configs]


@router.post("/configs/", response={201: FieldConfigOut}, auth=django_auth)
def create_config(request, payload: FieldConfigCreateIn):
    from userdefinedmodel.models import FieldConfig, ConfigLanguage, ConfigVersion
    if denied := _require_perms(request, "userdefinedmodel.add_fieldconfig"):
        return denied
    with transaction.atomic():
        cfg = FieldConfig.objects.create(name=payload.name, description=payload.description)
        for lang in payload.languages:
            ConfigLanguage.objects.create(
                config=cfg, code=lang.code, label=lang.label,
                is_default=lang.is_default, sort_order=lang.sort_order,
            )
        ConfigVersion.objects.create(config=cfg, status=ConfigVersion.Status.DRAFT)
    return 201, FieldConfigOut(
        id=cfg.id, name=cfg.name, description=cfg.description,
        created_at=cfg.created_at,
        last_published_at=None,
        version_count=1,
        stale_entity_count=0, entity_count=0, published_submodel_usage_count=0,
        type_ids=[],
        languages=[
            ConfigLanguageOut(code=l.code, label=l.label, is_default=l.is_default, sort_order=l.sort_order)
            for l in cfg.languages.all()
        ],
    )


@router.get("/configs/{config_id}/", response=FieldConfigOut, auth=django_auth)
def get_config(request, config_id: uuid.UUID):
    from userdefinedmodel.models import FieldConfig
    try:
        cfg = FieldConfig.objects.prefetch_related("languages", "user_defined_model_types").get(id=config_id)
    except FieldConfig.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return _field_config_out(cfg)


@router.patch("/configs/{config_id}/", response=FieldConfigOut, auth=django_auth)
def update_config(request, config_id: uuid.UUID, payload: FieldConfigUpdateIn):
    from userdefinedmodel.models import FieldConfig, ConfigLanguage
    if denied := _require_perms(request, "userdefinedmodel.change_fieldconfig"):
        return denied
    try:
        cfg = FieldConfig.objects.prefetch_related("languages", "user_defined_model_types").get(id=config_id)
    except FieldConfig.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    with transaction.atomic():
        if payload.name is not None:
            cfg.name = payload.name
        if payload.description is not None:
            cfg.description = payload.description
        cfg.save()
        # Languages: soft replace. Removing a language only deletes the
        # ConfigLanguage row — existing translations / field values for that
        # language code remain in the DB (orphaned but harmless; they simply
        # won't be shown or edited). Re-adding the same code re-enables them.
        if payload.languages is not None:
            cfg.languages.all().delete()
            for i, lang in enumerate(payload.languages):
                ConfigLanguage.objects.create(
                    config=cfg, code=lang.code, label=lang.label,
                    is_default=lang.is_default,
                    sort_order=lang.sort_order if lang.sort_order else i,
                )
    return _field_config_out(cfg)


@router.delete("/configs/{config_id}/", auth=django_auth)
def delete_config(request, config_id: uuid.UUID):
    from django.db.models.deletion import ProtectedError
    from userdefinedmodel.models import FieldConfig
    if denied := _require_perms(request, "userdefinedmodel.delete_fieldconfig"):
        return denied
    try:
        cfg = FieldConfig.objects.get(id=config_id)
    except FieldConfig.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if cfg.user_defined_model_types.exists():
        return JsonResponse({"detail": "Config is still in use by UDMTypes"}, status=400)
    if cfg.versions.filter(nodes__isnull=False).exists():
        return JsonResponse({"detail": "Config has entities referencing it"}, status=400)
    try:
        cfg.delete()
    except ProtectedError:
        return JsonResponse(
            {"detail": "Config cannot be deleted because it is still referenced by migration history or other configs. Remove those references first."},
            status=400,
        )
    return HttpResponse(status=204)


@router.get("/configs/{config_id}/versions/", auth=django_auth)
def list_config_versions(request, config_id: uuid.UUID):
    from django.db.models import Count, Q
    from userdefinedmodel.models import ConfigVersion, FieldConfig
    try:
        FieldConfig.objects.get(id=config_id)
    except FieldConfig.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    # Count only root entities pinned to each version (exclude submodel child nodes).
    versions = (
        ConfigVersion.objects.filter(config_id=config_id)
        .annotate(entity_count=Count(
            "nodes",
            filter=Q(nodes__userdefinedmodelentity__isnull=False),
            distinct=True,
        ))
        .order_by("-published_at", "-created_at")
    )
    return JsonResponse([
        {
            "id": str(v.id),
            "status": v.status,
            "notes": v.notes,
            "published_at": v.published_at.isoformat() if v.published_at else None,
            "created_at": v.created_at.isoformat(),
            "entity_count": v.entity_count,
        }
        for v in versions
    ], safe=False)


@router.get("/configs/{config_id}/versions/published/", response=ConfigVersionOut, auth=django_auth)
def get_published_version(request, config_id: uuid.UUID):
    from userdefinedmodel.models import ConfigVersion
    try:
        version = ConfigVersion.objects.get(config_id=config_id, status=ConfigVersion.Status.PUBLISHED)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No published version"}, status=404)
    return _serialize_config_version(version)


@router.get("/config-versions/{version_id}/", response=ConfigVersionOut, auth=django_auth)
def get_config_version(request, version_id: uuid.UUID):
    """Fetch a single config version by id (any status). Used to render an
    entity's form against its actual pinned version, even when archived."""
    from userdefinedmodel.models import ConfigVersion
    try:
        version = ConfigVersion.objects.get(id=version_id)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "Config version not found"}, status=404)
    return _serialize_config_version(version)


@router.get("/configs/{config_id}/versions/draft/", response=ConfigVersionOut, auth=django_auth)
def get_draft_version(request, config_id: uuid.UUID):
    from userdefinedmodel.models import ConfigVersion
    if denied := _require_perms(request, "userdefinedmodel.change_fieldconfig"):
        return denied
    try:
        version = ConfigVersion.objects.get(config_id=config_id, status=ConfigVersion.Status.DRAFT)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No draft version"}, status=404)
    return _serialize_config_version(version)


@router.get("/configs/{config_id}/versions/draft/as-input/", response=ConfigDraftExportOut, auth=django_auth)
def get_draft_as_input(request, config_id: uuid.UUID):
    """Return the draft config version in ConfigDraftIn shape for round-trip editing."""
    from userdefinedmodel.models import ConfigVersion
    if denied := _require_perms(request, "userdefinedmodel.change_fieldconfig"):
        return denied
    try:
        version = ConfigVersion.objects.get(config_id=config_id, status=ConfigVersion.Status.DRAFT)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No draft version"}, status=404)
    return _serialize_version_as_draft_in(version)


@router.put("/configs/{config_id}/versions/draft/", response=ConfigVersionOut, auth=django_auth)
def replace_draft(request, config_id: uuid.UUID, payload: ConfigDraftIn):
    from userdefinedmodel.models import (
        ConfigVersion, FieldConfig, FieldDefinition, FieldDefinitionTranslation,
        WorkflowVersion,
    )
    if denied := _require_perms(request, "userdefinedmodel.change_fieldconfig"):
        return denied
    try:
        cfg = FieldConfig.objects.get(id=config_id)
    except FieldConfig.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    with transaction.atomic():
        draft, _ = ConfigVersion.objects.get_or_create(
            config=cfg, status=ConfigVersion.Status.DRAFT,
            defaults={"notes": payload.notes},
        )
        draft.notes = payload.notes
        draft.save()
        draft.form_elements.all().delete()  # cascades to bindings + translations
        draft.field_definitions.all().delete()

        from userdefinedmodel.models import (
            DataField, FormElement, FormElementTranslation, FormElementBinding,
        )
        from userdefinedmodel.schemas import DataType as SchemaDataType, FormElementIn, FormElementBindingIn
        from userdefinedmodel.models.config import SlugIdSequence

        # Backward-compat: if the legacy `fields` key was sent (mixed data +
        # structural), split it into data_fields + form_elements.
        data_fields_in = list(payload.data_fields)
        form_elements_in = list(payload.form_elements)
        if payload.fields is not None:
            # Legacy shape: each entry is a data field; structural types go to
            # form_elements, data types to data_fields + a 1:1 'field' element.
            STRUCTURAL = SchemaDataType.__members__  # value set
            structural_set = {dt for dt in SchemaDataType if dt.value in {
                "tab_container","tab","save_button","hstack","hstack_group","tab_prev","tab_next",
            }}
            for fd_in in payload.fields:
                if fd_in.data_type in structural_set:
                    form_elements_in.append(FormElementIn(
                        slug=fd_in.slug,
                        element_type=fd_in.data_type.value,
                        parent_slug=fd_in.parent_slug,
                        sort_order=fd_in.sort_order or 0,
                        is_preview=fd_in.is_preview or False,
                        labels=fd_in.labels,
                        help_texts=fd_in.help_texts or {},
                        type_config=fd_in.type_config,
                        bindings=[],
                    ))
                else:
                    data_fields_in.append(fd_in)
                    form_elements_in.append(FormElementIn(
                        slug=fd_in.slug,
                        element_type="field",
                        parent_slug=fd_in.parent_slug,
                        sort_order=fd_in.sort_order or 0,
                        is_preview=fd_in.is_preview or False,
                        labels=fd_in.labels,
                        help_texts=fd_in.help_texts or {},
                        type_config={},
                        bindings=[FormElementBindingIn(data_field_slug=fd_in.slug, role="")],
                    ))

        # Validate SLUG_ID prefix uniqueness before creating data fields
        slug_id_prefixes: dict[str, str] = {}  # slug → prefix
        for fd_in in data_fields_in:
            if fd_in.data_type == SchemaDataType.SLUG_ID:
                prefix = (fd_in.type_config or {}).get("prefix", "")
                if prefix in slug_id_prefixes.values():
                    raise ApiError(400, {"detail": f"Duplicate SLUG_ID prefix '{prefix}' in this version"})
                slug_id_prefixes[fd_in.slug] = prefix
        for slug, prefix in slug_id_prefixes.items():
            conflict = SlugIdSequence.objects.filter(prefix=prefix).exclude(owner_config=cfg).exclude(owner_config__isnull=True).first()
            if conflict:
                raise ApiError(400, {"detail": f"Prefix '{prefix}' is already claimed by another config"})

        # Create data fields
        field_map = {}
        for fd_in in data_fields_in:
            submodel_config = None
            if fd_in.submodel_config_version_id:
                # Accept either a ConfigVersion id or a FieldConfig id; for a
                # FieldConfig id, default to its latest published version (the
                # up-to-date submodel schema) unless a specific version is given.
                sid = fd_in.submodel_config_version_id
                try:
                    submodel_config = ConfigVersion.objects.get(id=sid)
                except ConfigVersion.DoesNotExist:
                    # Try as a FieldConfig id → resolve to latest published version.
                    pub = ConfigVersion.objects.filter(
                        config_id=sid, status=ConfigVersion.Status.PUBLISHED
                    ).order_by("-published_at").first()
                    if pub is None:
                        raise ApiError(400, {"detail": f"ConfigVersion/Config {sid} not found or has no published version"})
                    submodel_config = pub

            workflow_version = None
            if fd_in.workflow_version_id:
                try:
                    workflow_version = WorkflowVersion.objects.get(id=fd_in.workflow_version_id)
                except WorkflowVersion.DoesNotExist:
                    raise ApiError(400, {"detail": f"WorkflowVersion {fd_in.workflow_version_id} not found"})

            fd = DataField.objects.create(
                version=draft,
                slug=fd_in.slug,
                data_type=fd_in.data_type.value,
                is_localized=fd_in.is_localized,
                submodel_config=submodel_config,
                workflow_version=workflow_version,
                type_config=fd_in.type_config,
            )
            field_map[fd_in.slug] = fd

            if fd_in.default is not None:
                err = _create_field_default(fd, fd_in.default, fd_in.is_localized)
                if err:
                    raise ApiError(400, {"errors": {fd_in.slug: [err]}})

        # Create form elements + translations + bindings
        element_map = {}
        for el_in in form_elements_in:
            el = FormElement.objects.create(
                version=draft,
                slug=el_in.slug,
                element_type=el_in.element_type,
                parent=None,  # resolved after all exist
                sort_order=el_in.sort_order,
                is_preview=el_in.is_preview,
                type_config=el_in.type_config,
            )
            element_map[el_in.slug] = el
            for lang, label in (el_in.labels or {}).items():
                help_text = el_in.help_texts.get(lang, "")
                FormElementTranslation.objects.create(
                    element=el, language=lang, label=label, help_text=help_text
                )
            for b in el_in.bindings:
                df = field_map.get(b.data_field_slug)
                if df is None:
                    raise ApiError(400, {"detail": (
                        f"Form element '{el_in.slug}' binds to data field '{b.data_field_slug}', "
                        f"but that data field is not present in this version. "
                        f"A data field cannot be deleted while a form element still references it; "
                        f"remove the binding first."
                    )})
                FormElementBinding.objects.create(form_element=el, data_field=df, role=b.role)

        # Resolve parents (slug -> FK) after all elements exist
        for el_in in form_elements_in:
            if el_in.parent_slug:
                parent = element_map.get(el_in.parent_slug)
                if parent is None:
                    raise ApiError(400, {"detail": f"Form element '{el_in.slug}' has unknown parent '{el_in.parent_slug}'"})
                element_map[el_in.slug].parent = parent
                element_map[el_in.slug].save(update_fields=["parent"])

        # Claim or re-confirm SLUG_ID sequence ownership for this config
        for slug, prefix in slug_id_prefixes.items():
            seq, _ = SlugIdSequence.objects.get_or_create(prefix=prefix, defaults={"owner_config": cfg})
            if seq.owner_config_id is None:
                seq.owner_config = cfg
                seq.save(update_fields=["owner_config"])

    return _serialize_config_version(draft)


@router.post("/configs/{config_id}/versions/draft/publish/", response=ConfigVersionOut, auth=django_auth)
def publish_draft(request, config_id: uuid.UUID):
    from userdefinedmodel.models import ConfigVersion, FieldConfig
    if denied := _require_perms(request, "userdefinedmodel.change_fieldconfig"):
        return denied
    try:
        cfg = FieldConfig.objects.get(id=config_id)
    except FieldConfig.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    try:
        draft = ConfigVersion.objects.get(config=cfg, status=ConfigVersion.Status.DRAFT)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No draft to publish"}, status=404)
    try:
        draft.publish()
    except ValidationError as exc:
        return JsonResponse({"errors": exc.message_dict if hasattr(exc, "message_dict") else str(exc)}, status=422)
    return _serialize_config_version(draft)
