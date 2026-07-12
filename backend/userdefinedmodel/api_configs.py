"""FieldConfig / ConfigVersion routes: /configs/..., /config-versions/..."""
from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from ninja import Router
from ninja.security import django_auth

from django.http import JsonResponse

from userdefinedmodel.api_helpers import (
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
    from userdefinedmodel.models import FieldConfig
    if denied := _require_perms(request, "userdefinedmodel.change_fieldconfig"):
        return denied
    try:
        cfg = FieldConfig.objects.prefetch_related("languages", "user_defined_model_types").get(id=config_id)
    except FieldConfig.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if payload.name is not None:
        cfg.name = payload.name
    if payload.description is not None:
        cfg.description = payload.description
    cfg.save()
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
    return JsonResponse({}, status=204)


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
        draft.field_definitions.all().delete()

        # Validate SLUG_ID prefix uniqueness before creating field definitions
        from userdefinedmodel.schemas import DataType as SchemaDataType
        from userdefinedmodel.models.config import SlugIdSequence
        slug_id_prefixes: dict[str, str] = {}  # slug → prefix
        for fd_in in payload.fields:
            if fd_in.data_type == SchemaDataType.SLUG_ID:
                prefix = (fd_in.type_config or {}).get("prefix", "")
                if prefix in slug_id_prefixes.values():
                    return JsonResponse({"detail": f"Duplicate SLUG_ID prefix '{prefix}' in this version"}, status=400)
                slug_id_prefixes[fd_in.slug] = prefix
        for slug, prefix in slug_id_prefixes.items():
            conflict = SlugIdSequence.objects.filter(prefix=prefix).exclude(owner_config=cfg).exclude(owner_config__isnull=True).first()
            if conflict:
                return JsonResponse({"detail": f"Prefix '{prefix}' is already claimed by another config"}, status=400)

        field_map = {}
        for fd_in in payload.fields:
            submodel_config = None
            if fd_in.submodel_config_version_id:
                try:
                    submodel_config = ConfigVersion.objects.get(id=fd_in.submodel_config_version_id)
                except ConfigVersion.DoesNotExist:
                    return JsonResponse({"detail": f"ConfigVersion {fd_in.submodel_config_version_id} not found"}, status=400)

            workflow_version = None
            if fd_in.workflow_version_id:
                try:
                    workflow_version = WorkflowVersion.objects.get(id=fd_in.workflow_version_id)
                except WorkflowVersion.DoesNotExist:
                    return JsonResponse({"detail": f"WorkflowVersion {fd_in.workflow_version_id} not found"}, status=400)

            fd = FieldDefinition.objects.create(
                version=draft,
                slug=fd_in.slug,
                data_type=fd_in.data_type.value,
                sort_order=fd_in.sort_order,
                is_localized=fd_in.is_localized,
                is_preview=fd_in.is_preview,
                parent_slug=fd_in.parent_slug or "",
                submodel_config=submodel_config,
                workflow_version=workflow_version,
                type_config=fd_in.type_config,
            )
            field_map[fd_in.slug] = fd

            for lang, label in (fd_in.labels or {}).items():
                help_text = fd_in.help_texts.get(lang, "")
                FieldDefinitionTranslation.objects.create(
                    field=fd, language=lang, label=label, help_text=help_text
                )

            if fd_in.default is not None:
                err = _create_field_default(fd, fd_in.default, fd_in.is_localized)
                if err:
                    return JsonResponse({"errors": {fd_in.slug: [err]}}, status=400)

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
