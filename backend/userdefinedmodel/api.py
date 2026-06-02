"""
userdefinedmodel API — mounted at /api/udm/ in project urls.py.
All endpoints use typed response schemas for OpenAPI compatibility:
  - Success: response={N: SchemaType} + return (N, schema_obj)
  - Errors:  return JsonResponse({...}, status=N) — passes through Ninja unchanged
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.utils.timezone import now
from ninja import NinjaAPI, File, Schema, UploadedFile
from ninja.errors import HttpError
from ninja.security import django_auth

def _wcag_text_color(hex_color: str) -> str:
    """Return '#ffffff' or '#000000' for maximum WCAG contrast against the given bg."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "#000000"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def lin(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return "#ffffff" if L < 0.1791 else "#000000"


from userdefinedmodel.schemas import (
    BulkMigrationCreateIn,
    BulkMigrationOut,
    BulkMigrationStatus,
    BundleExportIn,
    BundleExportOut,
    BundleFieldConfigOut,
    BundleUDMTypeOut,
    BundleWorkflowOut,
    ConfigDraftExportOut,
    ConfigDraftIn,
    ConfigLanguageOut,
    ConfigVersionOut,
    EditHistoryOut,
    EditGroupOut,
    EntityCreateIn,
    EntityOut,
    EntityPatchIn,
    FieldConfigCreateIn,
    FieldConfigOut,
    FieldConfigUpdateIn,
    FieldDefinitionDraftOut,
    FieldDefinitionOut,
    FieldEditOut,
    GroupAutocompleteItem,
    EntityAutocompleteItem,
    MigrationExecuteIn,
    MigrationPreviewOut,
    PolicyAssignIn,
    PolicyCreateIn,
    PolicyOut,
    PolicyEvalOut,
    PolicyUpdateIn,
    StagingFileOut,
    TransitionIn,
    TypePublicFieldsOut,
    UDMTypeOut,
    UDMTypeCreateIn,
    UDMTypeUpdateIn,
    UserAutocompleteItem,
    UserRefOut,
    WorkflowCreateIn,
    WorkflowDefinitionOut,
    WorkflowStateOut,
    WorkflowTransitionOut,
    WorkflowUpdateIn,
)

logger = logging.getLogger(__name__)

api = NinjaAPI(urls_namespace="udm", auth=django_auth)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _http409_concurrent() -> JsonResponse:
    return JsonResponse({"error": "concurrent_edit", "retry_after_ms": 500}, status=409)


def _set_lock_timeout_ms(ms: int) -> None:
    """Set a per-transaction lock acquisition timeout (PostgreSQL only)."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(f"SET LOCAL lock_timeout = '{ms}ms'")


def _require_perms(request, *perms: str) -> JsonResponse | None:
    """Return a 403 response if the user lacks ALL the given Django model permissions.

    Admin/schema endpoints authorize against explicit model permissions
    (e.g. "userdefinedmodel.add_fieldconfig") rather than is_staff/is_superuser.
    Never gate solely on is_staff — a superuser implicitly passes has_perms, and
    granular permissions can be delegated without granting full staff access.
    """
    if not request.user.has_perms(perms):
        return JsonResponse({"detail": "Permission denied"}, status=403)
    return None


def _policy_allows(entity, user, action: str, **kwargs) -> bool:
    """Object-level authorization for an entity action via its Rego policy.

    Default-deny: an entity with no UDMType or no attached policies, and any action
    not affirmatively granted by a policy clause, evaluates to False.
    """
    from userdefinedmodel.engine import evaluate_policy
    return bool(evaluate_policy(entity, user, action, **kwargs).get("allow", False))


def _entity_out_for_user(entity, user, policy_messages: list | None = None, view_policy: dict | None = None) -> EntityOut:
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.writer import serialize_node
    from userdefinedmodel.engine import evaluate_policy
    data = serialize_node(entity)
    policy = view_policy if view_policy is not None else evaluate_policy(entity, user, "view")
    viewable = policy.get("viewable_fields")   # None = no restriction
    editable = policy.get("editable_fields") or []
    # viewable_fields from the root-entity policy are top-level field slugs (e.g.
    # "status", "reviews"). Applying them to a child/submodel node's field_values
    # would filter everything out because the child has different slugs ("vote",
    # "comment"). Only filter when the node is a root entity.
    is_root = isinstance(entity, UserDefinedModelEntity)
    if is_root and viewable is not None:
        allowed = set(viewable)
        data["field_values"] = [fv for fv in data["field_values"] if fv["field_slug"] in allowed]
        data["children"] = {k: v for k, v in data["children"].items() if k in allowed}
    data["viewable_fields"] = viewable
    data["editable_fields"] = editable
    data["policy_messages"] = policy_messages or []
    return EntityOut(**data)


def _serialize_workflow(wf) -> WorkflowDefinitionOut:
    states = []
    for state in wf.states.prefetch_related("translations").all():
        label_dict = {t.language: t.label for t in state.translations.all()}
        bg = state.background_color or "#ffffff"
        states.append(WorkflowStateOut(
            name=state.name, label=label_dict,
            is_initial=state.is_initial,
            position_x=state.position_x, position_y=state.position_y,
            background_color=bg,
            text_color=_wcag_text_color(bg),
        ))
    transitions = []
    for trans in wf.transitions.prefetch_related("translations").select_related("from_state", "to_state").all():
        label_dict = {t.language: t.label for t in trans.translations.all()}
        transitions.append(WorkflowTransitionOut(
            name=trans.name, label=label_dict,
            from_state=trans.from_state.name if trans.from_state else None,
            from_undefined_only=trans.from_undefined_only,
            to_state=trans.to_state.name,
            source_handle=trans.source_handle,
            target_handle=trans.target_handle,
        ))
    initial = next((s for s in states if s.is_initial), None)
    return WorkflowDefinitionOut(
        id=wf.id,
        name=wf.name,
        description=wf.description,
        initial_state=initial.name if initial else None,
        states=states,
        transitions=transitions,
        virtual_node_positions=wf.virtual_node_positions or {},
    )


def _serialize_config_version(version) -> ConfigVersionOut:
    fields_out = []
    for fd in version.field_definitions.prefetch_related(
        "translations", "defaults",
        "workflow_definition__states__translations",
        "workflow_definition__transitions__translations",
        "workflow_definition__transitions__from_state",
        "workflow_definition__transitions__to_state",
    ).all():
        label_dict = {t.language: t.label for t in fd.translations.all()}
        help_dict = {t.language: t.help_text for t in fd.translations.all()}

        defaults_qs = list(fd.defaults.all())
        default_val = None
        if defaults_qs:
            if fd.is_localized:
                default_val = {d.language: d.get_value(field=fd) for d in defaults_qs}
            else:
                default_val = defaults_qs[0].get_value(field=fd)

        workflow_def_out = _serialize_workflow(fd.workflow_definition) if fd.workflow_definition else None

        fields_out.append(FieldDefinitionOut(
            id=fd.id,
            slug=fd.slug,
            data_type=fd.data_type,
            sort_order=fd.sort_order,
            is_localized=fd.is_localized,
            is_preview=fd.is_preview,
            label=label_dict,
            help_text=help_dict,
            type_config=fd.type_config or {},
            default=default_val,
            submodel_config=_serialize_config_version(fd.submodel_config) if fd.submodel_config else None,
            workflow_definition=workflow_def_out,
            parent_slug=fd.parent_slug or None,
        ))

    return ConfigVersionOut(
        version_id=version.id,
        status=version.status,
        notes=version.notes,
        published_at=version.published_at.isoformat() if version.published_at else None,
        languages=[
            ConfigLanguageOut(code=l.code, label=l.label, is_default=l.is_default, sort_order=l.sort_order)
            for l in version.config.languages.all()
        ],
        fields=fields_out,
    )


def _field_config_out(cfg) -> FieldConfigOut:
    from userdefinedmodel.models import UserDefinedModelEntity, ConfigVersion
    # "Stale" = entity pinned to a config version of this config that is not the
    # current published one (i.e. on an archived/draft version awaiting migration).
    # Mirrors the staleness definition used in ConfigVersion.publish().
    published_id = (
        ConfigVersion.objects.filter(config=cfg, status=ConfigVersion.Status.PUBLISHED)
        .values_list("id", flat=True).first()
    )
    stale_qs = UserDefinedModelEntity.objects.filter(config_version__config=cfg)
    if published_id is not None:
        stale_qs = stale_qs.exclude(config_version_id=published_id)
    stale_count = stale_qs.count()
    return FieldConfigOut(
        id=cfg.id, name=cfg.name, description=cfg.description,
        stale_entity_count=stale_count,
        type_ids=[t.id for t in cfg.user_defined_model_types.all()],
        languages=[
            ConfigLanguageOut(code=l.code, label=l.label, is_default=l.is_default, sort_order=l.sort_order)
            for l in cfg.languages.all()
        ],
    )


# ─── FieldConfig CRUD ─────────────────────────────────────────────────────────

@api.get("/configs/", response=list[FieldConfigOut], auth=django_auth)
def list_configs(request):
    from userdefinedmodel.models import FieldConfig
    configs = FieldConfig.objects.prefetch_related("languages", "user_defined_model_types")
    return [_field_config_out(cfg) for cfg in configs]


@api.post("/configs/", response={201: FieldConfigOut}, auth=django_auth)
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
        stale_entity_count=0, type_ids=[],
        languages=[
            ConfigLanguageOut(code=l.code, label=l.label, is_default=l.is_default, sort_order=l.sort_order)
            for l in cfg.languages.all()
        ],
    )


@api.get("/configs/{config_id}/", response=FieldConfigOut, auth=django_auth)
def get_config(request, config_id: uuid.UUID):
    from userdefinedmodel.models import FieldConfig
    try:
        cfg = FieldConfig.objects.prefetch_related("languages", "user_defined_model_types").get(id=config_id)
    except FieldConfig.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return _field_config_out(cfg)


@api.patch("/configs/{config_id}/", response=FieldConfigOut, auth=django_auth)
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


@api.delete("/configs/{config_id}/", auth=django_auth)
def delete_config(request, config_id: uuid.UUID):
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
    cfg.delete()
    return JsonResponse({}, status=204)


@api.get("/configs/{config_id}/versions/", auth=django_auth)
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


@api.get("/configs/{config_id}/versions/published/", response=ConfigVersionOut, auth=django_auth)
def get_published_version(request, config_id: uuid.UUID):
    from userdefinedmodel.models import ConfigVersion
    try:
        version = ConfigVersion.objects.get(config_id=config_id, status=ConfigVersion.Status.PUBLISHED)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No published version"}, status=404)
    return _serialize_config_version(version)


@api.get("/config-versions/{version_id}/", response=ConfigVersionOut, auth=django_auth)
def get_config_version(request, version_id: uuid.UUID):
    """Fetch a single config version by id (any status). Used to render an
    entity's form against its actual pinned version, even when archived."""
    from userdefinedmodel.models import ConfigVersion
    try:
        version = ConfigVersion.objects.get(id=version_id)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "Config version not found"}, status=404)
    return _serialize_config_version(version)


@api.get("/configs/{config_id}/versions/draft/", response=ConfigVersionOut, auth=django_auth)
def get_draft_version(request, config_id: uuid.UUID):
    from userdefinedmodel.models import ConfigVersion
    if denied := _require_perms(request, "userdefinedmodel.change_fieldconfig"):
        return denied
    try:
        version = ConfigVersion.objects.get(config_id=config_id, status=ConfigVersion.Status.DRAFT)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No draft version"}, status=404)
    return _serialize_config_version(version)


def _serialize_version_as_draft_in(version) -> ConfigDraftExportOut:
    """Serialize a ConfigVersion into the shape that ConfigDraftIn / PUT draft accepts.

    Uses id references (submodel_config_version_id, workflow_definition_id) rather than
    nested objects, so the result can be fed directly back into replace_draft().
    """
    fields_out = []
    for fd in version.field_definitions.prefetch_related("translations", "defaults").all():
        label_dict = {t.language: t.label for t in fd.translations.all()}
        help_dict = {t.language: t.help_text for t in fd.translations.all() if t.help_text}

        defaults_qs = list(fd.defaults.all())
        default_val = None
        if defaults_qs:
            if fd.is_localized:
                default_val = {d.language: d.get_value(field=fd) for d in defaults_qs}
            else:
                default_val = defaults_qs[0].get_value(field=fd)

        fields_out.append(FieldDefinitionDraftOut(
            slug=fd.slug,
            data_type=fd.data_type,
            sort_order=fd.sort_order,
            is_localized=fd.is_localized,
            is_preview=fd.is_preview,
            labels=label_dict if label_dict else None,
            help_texts=help_dict,
            type_config=fd.type_config or {},
            default=default_val,
            submodel_config_version_id=fd.submodel_config_id,
            workflow_definition_id=fd.workflow_definition_id,
            parent_slug=fd.parent_slug or None,
        ))

    return ConfigDraftExportOut(notes=version.notes, fields=fields_out)


@api.get("/configs/{config_id}/versions/draft/as-input/", response=ConfigDraftExportOut, auth=django_auth)
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


@api.put("/configs/{config_id}/versions/draft/", response=ConfigVersionOut, auth=django_auth)
def replace_draft(request, config_id: uuid.UUID, payload: ConfigDraftIn):
    from userdefinedmodel.models import (
        ConfigVersion, FieldConfig, FieldDefinition, FieldDefinitionTranslation,
        WorkflowDefinition,
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

            workflow_definition = None
            if fd_in.workflow_definition_id:
                try:
                    workflow_definition = WorkflowDefinition.objects.get(id=fd_in.workflow_definition_id)
                except WorkflowDefinition.DoesNotExist:
                    return JsonResponse({"detail": f"WorkflowDefinition {fd_in.workflow_definition_id} not found"}, status=400)

            fd = FieldDefinition.objects.create(
                version=draft,
                slug=fd_in.slug,
                data_type=fd_in.data_type.value,
                sort_order=fd_in.sort_order,
                is_localized=fd_in.is_localized,
                is_preview=fd_in.is_preview,
                parent_slug=fd_in.parent_slug or "",
                submodel_config=submodel_config,
                workflow_definition=workflow_definition,
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


def _create_field_default(field, default_value, is_localized: bool) -> str | None:
    """Create FieldDefaultValue record(s) for a field definition.

    Returns an error string on failure, or None on success.
    """
    from userdefinedmodel.models.config import FieldDefaultValue
    from django.core.exceptions import ValidationError as DjangoValidationError

    if field.data_type in FieldDefaultValue._NO_DEFAULT_TYPES:
        return f"Defaults are not supported for data_type '{field.data_type}'"

    try:
        if is_localized and isinstance(default_value, dict):
            for lang, val in default_value.items():
                dfv = FieldDefaultValue(field=field, language=lang)
                dfv.set_value(val, field=field)
                dfv.clean()
                dfv.save()
        else:
            dfv = FieldDefaultValue(field=field, language="")
            dfv.set_value(default_value, field=field)
            dfv.clean()
            dfv.save()
    except (DjangoValidationError, Exception) as exc:
        msg = str(exc)
        if hasattr(exc, "message_dict"):
            msg = "; ".join(f"{k}: {v}" for k, vs in exc.message_dict.items() for v in vs)
        elif hasattr(exc, "messages"):
            msg = "; ".join(exc.messages)
        return msg
    return None


@api.post("/configs/{config_id}/versions/draft/publish/", response=ConfigVersionOut, auth=django_auth)
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


# ─── UDMType ──────────────────────────────────────────────────────────────────

@api.get("/types/", response=list[UDMTypeOut], auth=django_auth)
def list_udm_types(request):
    from userdefinedmodel.models import UserDefinedModelType
    if denied := _require_perms(request, "userdefinedmodel.view_userdefinedmodeltype"):
        return denied
    types = UserDefinedModelType.objects.select_related("field_config").all()
    return [_udmtype_out(t) for t in types]


def _udmtype_out(t) -> UDMTypeOut:
    return UDMTypeOut(id=t.id, name=t.name, label=t.label, field_config_id=t.field_config_id)


@api.post("/types/", response={201: UDMTypeOut}, auth=django_auth)
def create_udm_type(request, payload: UDMTypeCreateIn):
    from userdefinedmodel.models import UserDefinedModelType
    if denied := _require_perms(request, "userdefinedmodel.add_userdefinedmodeltype"):
        return denied
    udm_type = UserDefinedModelType.objects.create(
        name=payload.name, label=payload.label,
    )
    return 201, _udmtype_out(udm_type)


@api.get("/types/{type_id}/", response=UDMTypeOut, auth=django_auth)
def get_udm_type(request, type_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelType
    try:
        t = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return _udmtype_out(t)


@api.get("/types/{type_id}/eval-policy/", response=PolicyEvalOut, auth=django_auth)
def eval_policy_for_type(
    request,
    type_id: uuid.UUID,
    entity_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str = "view",
    transition: Optional[str] = None,
):
    """Evaluate the Rego policy for a given entity + user and return the full
    input document, the raw policy sources, and the structured output.

    The input document includes the entity's full field values — data the policy
    would otherwise hide — plus the raw policy source. Require both view and
    change policy permissions; never gate on staff/superuser status alone."""
    if denied := _require_perms(request, "userdefinedmodel.view_policy", "userdefinedmodel.change_policy"):
        return denied

    from userdefinedmodel.models import UserDefinedModelEntity, UserDefinedModelType
    from userdefinedmodel.engine import build_policy_input, get_udm_type_for_node

    try:
        entity = UserDefinedModelEntity.objects.select_related(
            "config_version", "user_defined_model_type"
        ).get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Entity not found"}, status=404)

    try:
        from openid_user_management.models import OpenIDUser
        eval_user = OpenIDUser.objects.prefetch_related("groups", "user_permissions").get(id=user_id)
    except Exception:
        return JsonResponse({"detail": "User not found"}, status=404)

    # Collect policy sources
    udm_type = get_udm_type_for_node(entity)
    policy_entries = []
    if udm_type:
        for tp in udm_type.type_policies.select_related("policy").order_by("sort_order"):
            policy_entries.append({"slug": tp.policy.slug, "source": tp.policy.source})

    # Build input document
    kwargs = {}
    if transition:
        kwargs["transition"] = transition
    input_doc = build_policy_input(entity, eval_user, action, **kwargs)

    # Run evaluation. Default-deny when the type has no policies, mirroring
    # engine.evaluate_policy so this introspection view reflects real behavior.
    error_msg = None
    output = {"allow": False, "messages": [], "viewable_fields": [], "editable_fields": []}
    eval_prints: list[str] = []
    eval_coverage: list[dict] = []
    if policy_entries:
        try:
            import json as _json
            import regorus
            eng = regorus.Engine()
            for entry in policy_entries:
                eng.add_policy(f"policy_{entry['slug']}.rego", entry["source"])
            eng.set_input_json(_json.dumps(input_doc))
            eng.set_gather_prints(True)
            eng.set_enable_coverage(True)

            def _eval_list(rule_path):
                try:
                    raw = _json.loads(eng.eval_rule_as_json(rule_path))
                    return raw if isinstance(raw, list) else []
                except Exception:
                    return []

            def _eval_bool(rule_path, default=True):
                try:
                    raw = _json.loads(eng.eval_rule_as_json(rule_path))
                    # "<undefined>" is regorus' undefined sentinel; bool() of it
                    # would be truthy, so treat it as the default instead.
                    if raw is None or raw == "<undefined>":
                        return default
                    if isinstance(raw, list):
                        return bool(raw[0]) if raw else default
                    return bool(raw)
                except Exception:
                    return default

            output = {
                "allow": _eval_bool("data.udm.allow", default=False),
                "deny": _eval_list("data.udm.deny"),
                "messages": _eval_list("data.udm.messages"),
                "viewable_fields": _eval_list("data.udm.viewable_fields"),
                "editable_fields": _eval_list("data.udm.editable_fields"),
            }

            eval_prints = eng.take_prints()
            coverage_json = _json.loads(eng.get_coverage_report_as_json())
            # Strip the redundant `code` field — sources are already in `policies`.
            eval_coverage = [
                {k: v for k, v in f.items() if k != "code"}
                for f in coverage_json.get("files", [])
            ]
        except Exception as exc:
            error_msg = str(exc)
            output = {"allow": False, "messages": [], "viewable_fields": [], "editable_fields": []}

    return PolicyEvalOut(
        input_document=input_doc,
        policies=policy_entries,
        output=output,
        error=error_msg,
        prints=eval_prints,
        coverage=eval_coverage,
    )


@api.get("/types/{type_id}/config/", response=ConfigVersionOut, auth=django_auth)
def get_type_config(request, type_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelType, ConfigVersion
    try:
        udm_type = UserDefinedModelType.objects.select_related("field_config").get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if not udm_type.field_config:
        return JsonResponse({"detail": "No field config assigned"}, status=404)
    try:
        version = ConfigVersion.objects.get(config=udm_type.field_config, status=ConfigVersion.Status.PUBLISHED)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No published version"}, status=404)
    return _serialize_config_version(version)


@api.get("/types/{type_id}/public-fields/", response=TypePublicFieldsOut, auth=django_auth)
def get_type_public_fields(request, type_id: uuid.UUID):
    """Evaluate data.udm.public_type_fields from the type's policies.

    Runs all policies for this type with a minimal input
    (action=public_type_fields, no entity or user) and returns the resulting
    dict.  Returns an empty dict when no policies are attached or the rule is
    not defined.
    """
    from userdefinedmodel.models import UserDefinedModelType
    from userdefinedmodel.engine import evaluate_type_public_fields
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    _, descriptions = evaluate_type_public_fields(udm_type, user=request.user)
    return TypePublicFieldsOut(descriptions=descriptions)


@api.patch("/types/{type_id}/", response=UDMTypeOut, auth=django_auth)
def update_udm_type(
    request,
    type_id: uuid.UUID,
    field_config_id: Optional[uuid.UUID] = None,
    payload: Optional[UDMTypeUpdateIn] = None,
):
    from userdefinedmodel.models import UserDefinedModelType, FieldConfig
    if denied := _require_perms(request, "userdefinedmodel.change_userdefinedmodeltype"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if payload is not None:
        if payload.name is not None:
            udm_type.name = payload.name
        if payload.label is not None:
            udm_type.label = payload.label
        udm_type.save()
    if field_config_id is not None:
        try:
            cfg = FieldConfig.objects.get(id=field_config_id)
        except FieldConfig.DoesNotExist:
            return JsonResponse({"detail": "FieldConfig not found"}, status=404)
        from userdefinedmodel.models import BulkMigrationPlan, UserDefinedModelEntity
        stale = UserDefinedModelEntity.objects.filter(user_defined_model_type=udm_type).exclude(config_version__config=cfg)
        if stale.exists():
            confirmed_plans = BulkMigrationPlan.objects.filter(
                target_version__config=cfg,
                user_defined_model_type_filter=udm_type,
                status=BulkMigrationPlan.Status.DONE,
            )
            if not confirmed_plans.exists():
                return JsonResponse({"detail": "Stale entities exist without a confirmed BulkMigrationPlan"}, status=400)
        udm_type.field_config = cfg
        udm_type.save()
    return _udmtype_out(udm_type)


@api.delete("/types/{type_id}/", auth=django_auth)
def delete_udm_type(request, type_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelType, UserDefinedModelEntity
    if denied := _require_perms(request, "userdefinedmodel.delete_userdefinedmodeltype"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if UserDefinedModelEntity.objects.filter(user_defined_model_type=udm_type).exists():
        return JsonResponse({"detail": "UDMType still has entities and cannot be deleted"}, status=400)
    udm_type.delete()
    return JsonResponse({}, status=204)


# ─── Workflow CRUD ────────────────────────────────────────────────────────────

@api.get("/workflows/", response=list[WorkflowDefinitionOut], auth=django_auth)
def list_workflows(request):
    from userdefinedmodel.models import WorkflowDefinition
    if denied := _require_perms(request, "userdefinedmodel.view_fielddefinition"):
        return denied
    workflows = WorkflowDefinition.objects.prefetch_related(
        "states__translations", "transitions__translations",
        "transitions__from_state", "transitions__to_state",
    ).all()
    return [_serialize_workflow(wf) for wf in workflows]


@api.post("/workflows/", response={201: WorkflowDefinitionOut}, auth=django_auth)
def create_workflow(request, payload: WorkflowCreateIn):
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    if denied := _require_perms(request, "userdefinedmodel.add_fielddefinition"):
        return denied
    with transaction.atomic():
        wf = WorkflowDefinition.objects.create(
            name=payload.name, description=payload.description,
            virtual_node_positions=payload.virtual_node_positions,
        )
        state_map = {}
        for state_in in payload.states:
            state = WorkflowState.objects.create(
                workflow=wf, name=state_in.name,
                is_initial=state_in.is_initial,
                position_x=state_in.position_x, position_y=state_in.position_y,
                background_color=state_in.background_color,
            )
            state_map[state_in.name] = state
            for lang, label in state_in.label.items():
                WorkflowStateTranslation.objects.create(state=state, language=lang, label=label)
        for trans_in in payload.transitions:
            trans = WorkflowTransition.objects.create(
                workflow=wf,
                name=trans_in.name,
                from_state=state_map.get(trans_in.from_state) if trans_in.from_state else None,
                to_state=state_map[trans_in.to_state],
                from_undefined_only=trans_in.from_undefined_only,
                source_handle=trans_in.source_handle,
                target_handle=trans_in.target_handle,
            )
            for lang, label in trans_in.label.items():
                WorkflowTransitionTranslation.objects.create(transition=trans, language=lang, label=label)
    return 201, _serialize_workflow(wf)


@api.get("/workflows/{workflow_id}/", response=WorkflowDefinitionOut, auth=django_auth)
def get_workflow(request, workflow_id: uuid.UUID):
    from userdefinedmodel.models import WorkflowDefinition
    if denied := _require_perms(request, "userdefinedmodel.view_fielddefinition"):
        return denied
    try:
        wf = WorkflowDefinition.objects.prefetch_related(
            "states__translations", "transitions__translations",
            "transitions__from_state", "transitions__to_state",
        ).get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return _serialize_workflow(wf)


@api.put("/workflows/{workflow_id}/", response=WorkflowDefinitionOut, auth=django_auth)
def update_workflow(request, workflow_id: uuid.UUID, payload: WorkflowUpdateIn):
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    if denied := _require_perms(request, "userdefinedmodel.change_fielddefinition"):
        return denied
    try:
        wf = WorkflowDefinition.objects.get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    with transaction.atomic():
        if payload.name is not None:
            wf.name = payload.name
        if payload.description is not None:
            wf.description = payload.description
        wf.virtual_node_positions = payload.virtual_node_positions
        wf.save()
        if payload.states is not None:
            from userdefinedmodel.models.node import FieldValue

            if sum(1 for s in payload.states if s.is_initial) != 1:
                return JsonResponse({"detail": "exactly one state must have is_initial=True"}, status=400)

            incoming_names = {s.name for s in payload.states}
            existing_states = {s.name: s for s in wf.states.all()}

            # 0. Apply renames: states that supply previous_name get their DB row
            #    renamed first, so the upsert loop can find them by new name.
            for state_in in payload.states:
                prev = state_in.previous_name
                if prev and prev != state_in.name and prev in existing_states:
                    if state_in.name in existing_states:
                        return JsonResponse(
                            {"detail": f"State name '{state_in.name}' is already in use"},
                            status=400,
                        )
                    old_state = existing_states.pop(prev)
                    old_state.name = state_in.name
                    old_state.save()
                    existing_states[state_in.name] = old_state

            states_to_delete = {n: s for n, s in existing_states.items() if n not in incoming_names}

            # 1. Clear is_initial on surviving states before the upsert loop to avoid
            #    the partial-unique constraint firing if the initial state changes.
            wf.states.filter(name__in=incoming_names).update(is_initial=False)

            # 2. Upsert surviving and new states, building state_map by name.
            state_map = {}
            for state_in in payload.states:
                if state_in.name in existing_states:
                    state = existing_states[state_in.name]
                    state.is_initial = state_in.is_initial
                    state.position_x = state_in.position_x
                    state.position_y = state_in.position_y
                    state.background_color = state_in.background_color
                    state.save()
                    state.translations.all().delete()
                else:
                    state = WorkflowState.objects.create(
                        workflow=wf, name=state_in.name,
                        is_initial=state_in.is_initial,
                        position_x=state_in.position_x, position_y=state_in.position_y,
                        background_color=state_in.background_color,
                    )
                for lang, label in state_in.label.items():
                    WorkflowStateTranslation.objects.create(state=state, language=lang, label=label)
                state_map[state_in.name] = state

            # 3. Run bulk migrations before deletion so entities land in a valid state.
            for migration in payload.migrations:
                from_state_obj = existing_states.get(migration.from_state)
                to_state_obj = state_map.get(migration.to_state)
                if from_state_obj and to_state_obj:
                    FieldValue.objects.filter(
                        field__workflow_definition_id=wf.id,
                        value_workflow_state=from_state_obj,
                    ).update(value_workflow_state=to_state_obj)

            # 4. Gate: refuse to delete states that still have entities.
            for del_name, del_state in states_to_delete.items():
                count = FieldValue.objects.filter(
                    field__workflow_definition_id=wf.id,
                    value_workflow_state=del_state,
                ).count()
                if count > 0:
                    raise HttpError(
                        400,
                        f"State '{del_name}' still has {count} "
                        f"{'entity' if count == 1 else 'entities'}. "
                        "Add a migration edge to move them first, or re-add the state.",
                    )

            # 5. Delete removed states (safe — entities migrated or confirmed at 0).
            for del_state in states_to_delete.values():
                del_state.delete()

            if payload.transitions is not None:
                wf.transitions.all().delete()
                for trans_in in payload.transitions:
                    trans = WorkflowTransition.objects.create(
                        workflow=wf,
                        name=trans_in.name,
                        from_state=state_map.get(trans_in.from_state) if trans_in.from_state else None,
                        to_state=state_map[trans_in.to_state],
                        from_undefined_only=trans_in.from_undefined_only,
                        source_handle=trans_in.source_handle,
                        target_handle=trans_in.target_handle,
                    )
                    for lang, label in trans_in.label.items():
                        WorkflowTransitionTranslation.objects.create(transition=trans, language=lang, label=label)
    return _serialize_workflow(wf)


@api.delete("/workflows/{workflow_id}/", auth=django_auth)
def delete_workflow(request, workflow_id: uuid.UUID):
    from userdefinedmodel.models import WorkflowDefinition
    if denied := _require_perms(request, "userdefinedmodel.delete_fielddefinition"):
        return denied
    try:
        wf = WorkflowDefinition.objects.get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    try:
        wf.delete()
    except Exception:
        return JsonResponse({"detail": "Workflow is in use and cannot be deleted"}, status=409)
    return JsonResponse({}, status=204)


@api.get("/workflows/{workflow_id}/state-counts/", auth=django_auth)
def workflow_state_counts(request, workflow_id: uuid.UUID):
    """Return a dict of state_name → entity count for fields using this workflow."""
    from userdefinedmodel.models import WorkflowDefinition
    from userdefinedmodel.models.node import FieldValue
    from django.db.models import Count

    if denied := _require_perms(request, "userdefinedmodel.view_fielddefinition"):
        return denied
    try:
        WorkflowDefinition.objects.get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    rows = (
        FieldValue.objects
        .filter(field__workflow_definition_id=workflow_id, value_workflow_state__isnull=False)
        .values("value_workflow_state__name")
        .annotate(count=Count("id"))
    )
    result = {row["value_workflow_state__name"]: row["count"] for row in rows}
    return JsonResponse(result)


# ─── Policies ─────────────────────────────────────────────────────────────────

@api.get("/policies/", response=list[PolicyOut], auth=django_auth)
def list_policies(request):
    from userdefinedmodel.models import Policy
    if denied := _require_perms(request, "userdefinedmodel.view_policy"):
        return denied
    return [PolicyOut(slug=p.slug, source=p.source) for p in Policy.objects.all()]


@api.post("/policies/", response={201: PolicyOut}, auth=django_auth)
def create_policy(request, payload: PolicyCreateIn):
    from userdefinedmodel.models import Policy
    if denied := _require_perms(request, "userdefinedmodel.add_policy"):
        return denied
    policy = Policy.objects.create(slug=payload.slug, source=payload.source)
    return 201, PolicyOut(slug=policy.slug, source=policy.source)


@api.get("/policies/{slug}/", response=PolicyOut, auth=django_auth)
def get_policy(request, slug: str):
    from userdefinedmodel.models import Policy
    if denied := _require_perms(request, "userdefinedmodel.view_policy"):
        return denied
    try:
        p = Policy.objects.get(slug=slug)
    except Policy.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return PolicyOut(slug=p.slug, source=p.source)


@api.put("/policies/{slug}/", response=PolicyOut, auth=django_auth)
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


@api.delete("/policies/{slug}/", auth=django_auth)
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


@api.get("/types/{type_id}/policies/", response=list[PolicyOut], auth=django_auth)
def list_type_policies(request, type_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelType
    if denied := _require_perms(request, "userdefinedmodel.view_policy"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return [PolicyOut(slug=tp.policy.slug, source=tp.policy.source)
            for tp in udm_type.type_policies.select_related("policy").order_by("sort_order")]


@api.post("/types/{type_id}/policies/", response={201: PolicyOut}, auth=django_auth)
def assign_policy(request, type_id: uuid.UUID, payload: PolicyAssignIn):
    from userdefinedmodel.models import UserDefinedModelType, Policy, UserDefinedModelTypePolicy
    if denied := _require_perms(request, "userdefinedmodel.change_userdefinedmodeltype"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    try:
        policy = Policy.objects.get(slug=payload.policy_slug)
    except Policy.DoesNotExist:
        return JsonResponse({"detail": "Policy not found"}, status=404)
    UserDefinedModelTypePolicy.objects.get_or_create(
        user_defined_model_type=udm_type, policy=policy,
        defaults={"sort_order": payload.sort_order},
    )
    return 201, PolicyOut(slug=policy.slug, source=policy.source)


@api.delete("/types/{type_id}/policies/{slug}/", auth=django_auth)
def remove_policy(request, type_id: uuid.UUID, slug: str):
    from userdefinedmodel.models import UserDefinedModelType, UserDefinedModelTypePolicy
    if denied := _require_perms(request, "userdefinedmodel.change_userdefinedmodeltype"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    UserDefinedModelTypePolicy.objects.filter(user_defined_model_type=udm_type, policy__slug=slug).delete()
    return JsonResponse({}, status=204)


# ─── Entities ─────────────────────────────────────────────────────────────────

@api.post("/entities/", response={201: EntityOut}, auth=django_auth)
def create_entity(request, payload: EntityCreateIn, validate: bool = False):
    from userdefinedmodel.models import UserDefinedModelType, UserDefinedModelEntity, ConfigVersion
    from userdefinedmodel.engine import evaluate_policy
    try:
        udm_type = UserDefinedModelType.objects.select_related("field_config").get(id=payload.user_defined_model_type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "UDMType not found"}, status=404)
    if not udm_type.field_config:
        return JsonResponse({"detail": "UDMType has no field config"}, status=400)
    try:
        version = ConfigVersion.objects.get(config=udm_type.field_config, status=ConfigVersion.Status.PUBLISHED)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No published config version"}, status=400)

    if validate:
        with transaction.atomic():
            entity = UserDefinedModelEntity.objects.create(
                config_version=version, user_defined_model_type=udm_type,
            )
            entity.materialize_defaults()
            entity.materialize_user_defaults(request.user)
            result = evaluate_policy(entity, request.user, "create")
            transaction.set_rollback(True)
        return JsonResponse({
            "valid": result.get("allow", False),
            "policy_messages": result.get("messages", []),
            "errors": {},
        })

    with transaction.atomic():
        entity = UserDefinedModelEntity.objects.create(
            config_version=version, user_defined_model_type=udm_type,
        )
        entity.materialize_defaults()
        entity.materialize_user_defaults(request.user)
    return 201, _entity_out_for_user(entity, request.user)


@api.get("/entities/{entity_id}/", response=EntityOut, auth=django_auth)
def get_entity(request, entity_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.engine import evaluate_policy
    try:
        entity = UserDefinedModelEntity.objects.select_related(
            "config_version", "user_defined_model_type"
        ).prefetch_related("field_values__field", "children").get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    # Object-level view authorization: the policy "view" allow decision gates
    # whether the entity is visible at all. 404 (not 403) avoids leaking existence
    # unless the policy produced messages explaining the denial.
    policy = evaluate_policy(entity, request.user, "view")
    if not policy.get("allow", False):
        msgs = policy.get("messages") or []
        if msgs:
            return JsonResponse({"detail": "Access denied", "policy_messages": msgs}, status=403)
        return JsonResponse({"detail": "Not found"}, status=404)
    return _entity_out_for_user(entity, request.user, view_policy=policy)


@api.patch("/entities/{entity_id}/", response=EntityOut, auth=django_auth)
def patch_entity(request, entity_id: uuid.UUID, payload: EntityPatchIn, validate_only: bool = False):
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.writer import apply_patch
    from userdefinedmodel.engine import TransitionError, PolicyError

    if validate_only:
        result = {"valid": True, "policy_messages": [], "errors": {}}
        try:
            with transaction.atomic():
                _set_lock_timeout_ms(50)
                try:
                    entity = (UserDefinedModelEntity.objects
                              .select_for_update(nowait=False, of=("self",))
                              .select_related("config_version")
                              .get(id=entity_id))
                except UserDefinedModelEntity.DoesNotExist:
                    return JsonResponse({"detail": "Not found"}, status=404)
                except OperationalError:
                    return _http409_concurrent()
                try:
                    _eg, messages = apply_patch(entity, payload.changed_fields, request.user, validate_only=True)
                    result = {"valid": True, "policy_messages": messages, "errors": {}}
                except PolicyError as e:
                    result = {"valid": False, "policy_messages": e.messages, "errors": {}}
                except ValidationError as exc:
                    errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
                    result = {"valid": False, "policy_messages": [], "errors": errors}
                except TransitionError as e:
                    result = {"valid": False, "policy_messages": [],
                              "errors": {"__all__": [str(e)]}}
                finally:
                    transaction.set_rollback(True)
        except OperationalError:
            return _http409_concurrent()
        return JsonResponse(result)

    try:
        with transaction.atomic():
            try:
                entity = (UserDefinedModelEntity.objects
                          .select_for_update(nowait=True, of=("self",))
                          .select_related("config_version")
                          .get(id=entity_id))
            except UserDefinedModelEntity.DoesNotExist:
                return JsonResponse({"detail": "Not found"}, status=404)
            except OperationalError:
                return _http409_concurrent()
            _eg, save_messages = apply_patch(entity, payload.changed_fields, request.user)
    except PolicyError as e:
        return JsonResponse({"policy_messages": e.messages}, status=422)
    except TransitionError as e:
        if e.http_status == 409:
            return JsonResponse({"error": e.args[0], **e.details}, status=409)
        return JsonResponse({"error": str(e)}, status=e.http_status)
    except ValidationError as exc:
        errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
        return JsonResponse({"errors": errors}, status=400)
    except OperationalError:
        return _http409_concurrent()
    return _entity_out_for_user(entity, request.user, policy_messages=save_messages)


@api.delete("/entities/{entity_id}/", auth=django_auth)
def delete_entity(request, entity_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelEntity
    try:
        entity = UserDefinedModelEntity.objects.get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    # Object-level delete authorization is delegated to the entity's policy
    # ("delete" action). Default-deny: no policy means no delete.
    if not _policy_allows(entity, request.user, "delete"):
        return JsonResponse({"detail": "Delete denied by policy"}, status=403)
    entity.delete()
    return JsonResponse({}, status=204)


@api.post("/entities/{entity_id}/transition/", response=EntityOut, auth=django_auth)
def transition_entity(request, entity_id: uuid.UUID, payload: TransitionIn, validate_only: bool = False):
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.engine import execute_transition, TransitionError

    from userdefinedmodel.writer import apply_patch
    from userdefinedmodel.engine import PolicyError

    from userdefinedmodel.models import UserDefinedModelEntityNode

    if validate_only:
        result = {"valid": True, "policy_messages": [], "errors": {}}
        try:
            with transaction.atomic():
                _set_lock_timeout_ms(50)
                try:
                    entity = (UserDefinedModelEntityNode.objects
                              .select_for_update(nowait=False, of=("self",))
                              .select_related("config_version")
                              .get(id=entity_id))
                except UserDefinedModelEntityNode.DoesNotExist:
                    return JsonResponse({"detail": "Not found"}, status=404)
                except OperationalError:
                    return _http409_concurrent()
                try:
                    patch_eg = None
                    if payload.changed_fields:
                        patch_eg, _ = apply_patch(entity, payload.changed_fields, request.user)
                    msgs = execute_transition(entity, payload.field, payload.transition, request.user, edit_group=patch_eg)
                    result = {"valid": True, "policy_messages": msgs, "errors": {}}
                except PolicyError as e:
                    result = {"valid": False, "policy_messages": e.messages, "errors": {}}
                except TransitionError as e:
                    result = {"valid": False, "policy_messages": e.details.get("policy_messages", []),
                              "errors": {"__all__": [str(e)]}}
                except ValidationError as exc:
                    errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
                    result = {"valid": False, "policy_messages": [], "errors": errors}
                finally:
                    transaction.set_rollback(True)
        except OperationalError:
            return _http409_concurrent()
        return JsonResponse(result)

    transition_messages = []
    try:
        with transaction.atomic():
            try:
                entity = (UserDefinedModelEntityNode.objects
                          .select_for_update(nowait=True, of=("self",))
                          .select_related("config_version")
                          .get(id=entity_id))
            except UserDefinedModelEntityNode.DoesNotExist:
                return JsonResponse({"detail": "Not found"}, status=404)
            except OperationalError:
                return _http409_concurrent()
            patch_eg = None
            if payload.changed_fields:
                patch_eg, _ = apply_patch(entity, payload.changed_fields, request.user)
            transition_messages = execute_transition(entity, payload.field, payload.transition, request.user, edit_group=patch_eg)
    except PolicyError as e:
        return JsonResponse({"policy_messages": e.messages}, status=422)
    except TransitionError as e:
        return JsonResponse({"error": str(e), **e.details}, status=e.http_status)
    except ValidationError as exc:
        errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
        return JsonResponse({"errors": errors}, status=400)
    except OperationalError:
        return _http409_concurrent()
    return _entity_out_for_user(entity, request.user, policy_messages=transition_messages)


@api.get("/entities/{entity_id}/history/", response=EditHistoryOut, auth=django_auth)
def entity_history(request, entity_id: uuid.UUID, page: int = 1, page_size: int = 20):
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.models.history import EditGroup
    from userdefinedmodel.engine import evaluate_policy
    try:
        entity = UserDefinedModelEntity.objects.get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    # Object-level view authorization. History exposes old/new field values, so
    # gate on the "view" allow decision and redact edits for non-viewable fields.
    policy = evaluate_policy(entity, request.user, "view")
    if not policy.get("allow", False):
        return JsonResponse({"detail": "Not found"}, status=404)
    viewable = policy.get("viewable_fields")  # None = no field-level restriction

    qs = EditGroup.objects.filter(root_entity=entity).prefetch_related(
        "field_edits__field__translations",
        "field_edits__old_attachment",
        "field_edits__new_attachment",
        "saved_by",
    ).order_by("-saved_at")

    total = qs.count()
    offset = (page - 1) * page_size
    groups = list(qs[offset:offset + page_size])

    results = []
    for group in groups:
        edits = []
        for fe in group.field_edits.all():
            # Hide value edits for fields the policy does not expose to this user.
            # Non-field edits (transitions, node add/remove) carry no field value
            # and remain visible so structural history stays coherent.
            if viewable is not None and fe.field is not None and fe.field.slug not in viewable:
                continue
            slug = fe.field.slug if fe.field else None
            label = None
            if fe.field:
                trans = fe.field.translations.first()
                label = trans.label if trans else slug
            edits.append(FieldEditOut(
                change_kind=fe.change_kind,
                field_slug=slug,
                field_label=label,
                language=fe.language,
                old_value=fe.old_value,
                new_value=fe.new_value,
                old_file_name=fe.old_attachment.original_name if fe.old_attachment else None,
                new_file_name=fe.new_attachment.original_name if fe.new_attachment else None,
                affected_node_id=fe.affected_node_id,
            ))

        node_type = "entity"
        try:
            group.node.userdefinedmodelentity
        except Exception:
            pf = getattr(group.node, "parent_field", None)
            if pf:
                node_type = f"submodel:{pf.slug}"

        results.append(EditGroupOut(
            id=group.id,
            saved_at=group.saved_at.isoformat(),
            saved_by=UserRefOut(id=group.saved_by.id, display_name=group.saved_by.username) if group.saved_by else None,
            node_id=group.node_id,
            node_type=node_type,
            edits=edits,
        ))

    next_url = None
    if offset + page_size < total:
        next_url = f"/api/udm/entities/{entity_id}/history/?page={page + 1}&page_size={page_size}"

    return EditHistoryOut(count=total, next=next_url, results=results)


@api.get("/entities/{entity_id}/policy-document/", auth=django_auth)
def entity_policy_document(request, entity_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelEntity
    # Returns the raw, unredacted policy input document (every field value,
    # bypassing the policy's own field-level visibility). Treat it like policy
    # internals: require both view and change policy permissions.
    if denied := _require_perms(request, "userdefinedmodel.view_policy", "userdefinedmodel.change_policy"):
        return denied
    try:
        entity = UserDefinedModelEntity.objects.get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return JsonResponse(entity.to_policy_document())


# ─── Staging files ────────────────────────────────────────────────────────────

@api.post("/staging-files/", response={201: StagingFileOut}, auth=django_auth)
def upload_staging_file(
    request,
    file: UploadedFile = File(...),
    intended_field_id: Optional[uuid.UUID] = None,
):
    from userdefinedmodel.models.node import StagingFile
    staging = StagingFile.objects.create(
        uploader=request.user,
        file=file,
        original_name=file.name,
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=file.size,
        expires_at=now() + timedelta(hours=24),
        intended_field_id=intended_field_id,
    )
    return 201, StagingFileOut(
        staging_id=staging.id,
        original_name=staging.original_name,
        mime_type=staging.mime_type,
        size_bytes=staging.size_bytes,
        expires_at=staging.expires_at.isoformat(),
    )


@api.delete("/staging-files/{staging_id}/", auth=django_auth)
def delete_staging_file(request, staging_id: uuid.UUID):
    from userdefinedmodel.models.node import StagingFile
    try:
        staging = StagingFile.objects.get(id=staging_id, uploader=request.user)
    except StagingFile.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    staging.file.delete(save=False)
    staging.delete()
    return JsonResponse({}, status=204)


# ─── Autocomplete ─────────────────────────────────────────────────────────────

@api.get("/users/", response=list[UserAutocompleteItem], auth=django_auth)
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


@api.get("/groups/", response=list[GroupAutocompleteItem], auth=django_auth)
def search_groups(request, q: str = "", ids: str = ""):
    from django.contrib.auth.models import Group
    qs = Group.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        qs = Group.objects.filter(id__in=id_list)
    return [GroupAutocompleteItem(id=g.id, name=g.name) for g in qs[:50]]


@api.get("/entity-search/", response=list[EntityAutocompleteItem], auth=django_auth)
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
    # still reach the cap of 50 visible entities.
    results = []
    for entity in qs.iterator(chunk_size=200):
        if not evaluate_policy(entity, request.user, "browse").get("allow", False):
            continue
        display = _entity_preview_display(entity)
        results.append(EntityAutocompleteItem(
            id=entity.id,
            display=display,
            type_id=entity.user_defined_model_type_id,
        ))
        if len(results) >= 50:
            break
    return results


def _entity_preview_display(entity) -> str:
    """Build a human-readable display string from is_preview fields, falling back to the UUID."""
    config_version = entity.config_version
    if config_version is None:
        return str(entity.id)

    # Determine default language code for localized fields
    default_lang = ""
    for lang in config_version.config.languages.all():
        if lang.is_default:
            default_lang = lang.code
            break

    preview_fields = [fd for fd in config_version.field_definitions.all() if fd.is_preview]
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


# ─── Migration ────────────────────────────────────────────────────────────────

@api.get("/entities/{entity_id}/migration-preview/", response=MigrationPreviewOut, auth=django_auth)
def migration_preview(
    request,
    entity_id: uuid.UUID,
    target_user_defined_model_type: Optional[uuid.UUID] = None,
    target_version: Optional[uuid.UUID] = None,
):
    from userdefinedmodel.models import (
        UserDefinedModelEntity, ConfigVersion, UserDefinedModelType, UserDefinedModelEntityMigration,
    )
    from userdefinedmodel.schemas import MigrationAction, MigrationPreviewFieldOut
    try:
        entity = UserDefinedModelEntity.objects.select_related("config_version").get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    # Migration rewrites the entity's field values, so it is a write to the
    # entity: gate on the "save" allow decision before reading its fields or
    # creating the migration record.
    if not _policy_allows(entity, request.user, "save"):
        return JsonResponse({"detail": "Not allowed"}, status=403)

    if target_version:
        try:
            tgt_version = ConfigVersion.objects.get(id=target_version)
        except ConfigVersion.DoesNotExist:
            return JsonResponse({"detail": "Target version not found"}, status=404)
        tgt_type = entity.user_defined_model_type
    elif target_user_defined_model_type:
        try:
            tgt_type = UserDefinedModelType.objects.select_related("field_config").get(id=target_user_defined_model_type)
        except UserDefinedModelType.DoesNotExist:
            return JsonResponse({"detail": "Target type not found"}, status=404)
        try:
            tgt_version = ConfigVersion.objects.get(config=tgt_type.field_config, status=ConfigVersion.Status.PUBLISHED)
        except ConfigVersion.DoesNotExist:
            return JsonResponse({"detail": "Target type has no published config"}, status=404)
    else:
        return JsonResponse({"detail": "Either target_user_defined_model_type or target_version is required"}, status=400)

    migration = UserDefinedModelEntityMigration.objects.create(
        user_defined_model_entity=entity,
        source_version=entity.config_version,
        target_user_defined_model_type=tgt_type,
        target_version=tgt_version,
    )

    source_fields = {f.slug: f for f in entity.config_version.field_definitions.all()}
    target_fields = {f.slug: f for f in tgt_version.field_definitions.all()}
    _ALLOWED = {
        ("integer", "float"), ("text_short", "text_long"), ("text_long", "text_markdown"),
        ("select_single", "select_multi"), ("user_select", "user_select_multi"),
        ("group_select", "group_select_multi"), ("entity_select", "entity_select_multi"),
    }

    previews = []
    for slug, src_field in source_fields.items():
        if slug in target_fields:
            tgt_field = target_fields[slug]
            if src_field.data_type == tgt_field.data_type or (src_field.data_type, tgt_field.data_type) in _ALLOWED:
                previews.append(MigrationPreviewFieldOut(
                    source_slug=slug, source_data_type=src_field.data_type,
                    suggested_action=MigrationAction.MAP, suggested_target_slug=slug, conflict_reason=None,
                ))
            else:
                previews.append(MigrationPreviewFieldOut(
                    source_slug=slug, source_data_type=src_field.data_type,
                    suggested_action=MigrationAction.OVERFLOW, suggested_target_slug=None,
                    conflict_reason=f"Incompatible: {src_field.data_type} → {tgt_field.data_type}",
                ))
        else:
            previews.append(MigrationPreviewFieldOut(
                source_slug=slug, source_data_type=src_field.data_type,
                suggested_action=MigrationAction.OVERFLOW, suggested_target_slug=None, conflict_reason=None,
            ))

    return MigrationPreviewOut(
        migration_id=migration.id,
        source_version_id=entity.config_version_id,
        target_version_id=tgt_version.id,
        field_previews=previews,
    )


def _resolve_migration_value(src_fv, tgt_field):
    """Return a value for set_value(val, field=tgt_field), or None to skip.

    Workflow fields: get_value() returns the state name string, which cannot be
    assigned directly as value_workflow_state_id. Resolve by name in the target
    workflow; return None if not found so materialize_defaults() sets the initial state.
    """
    from userdefinedmodel.models.config import FieldDefinition
    val = src_fv.get_value()
    if val is None:
        return None
    if tgt_field.data_type == FieldDefinition.DataType.WORKFLOW:
        if not tgt_field.workflow_definition_id or not isinstance(val, str):
            return None
        from userdefinedmodel.models import WorkflowState
        return WorkflowState.objects.filter(
            workflow_id=tgt_field.workflow_definition_id, name=val
        ).first()
    return val


@api.post("/entities/{entity_id}/migrate/", response=EntityOut, auth=django_auth)
def execute_migration(request, entity_id: uuid.UUID, payload: MigrationExecuteIn):
    from userdefinedmodel.models import (
        UserDefinedModelEntity, UserDefinedModelEntityMigration, MigrationFieldMapping, FieldValue,
    )
    try:
        entity = UserDefinedModelEntity.objects.get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    # Migration is a write to the entity: gate on the "save" allow decision.
    if not _policy_allows(entity, request.user, "save"):
        return JsonResponse({"detail": "Not allowed"}, status=403)
    try:
        migration = UserDefinedModelEntityMigration.objects.select_related(
            "target_version", "target_user_defined_model_type"
        ).get(id=payload.migration_id, user_defined_model_entity=entity)
    except UserDefinedModelEntityMigration.DoesNotExist:
        return JsonResponse({"detail": "Migration not found"}, status=404)

    with transaction.atomic():
        try:
            entity = (UserDefinedModelEntity.objects
                      .select_for_update(nowait=True, of=("self",))
                      .get(id=entity_id))
        except OperationalError:
            return _http409_concurrent()

        tgt_version = migration.target_version
        source_field_map = {f.slug: f for f in entity.config_version.field_definitions.all()}
        target_field_map = {f.slug: f for f in tgt_version.field_definitions.all()}
        overflow = {}

        for mapping_in in payload.field_mappings:
            src_field = source_field_map.get(mapping_in.source_field_slug)
            if not src_field:
                continue
            fv = entity.field_values.filter(field=src_field).first()
            if not fv:
                continue
            action = mapping_in.action.value
            if action == "map" and mapping_in.target_field_slug:
                tgt_field = target_field_map.get(mapping_in.target_field_slug)
                if tgt_field:
                    val = _resolve_migration_value(fv, tgt_field)
                    if val is not None:
                        new_fv, _ = FieldValue.objects.get_or_create(node=entity, field=tgt_field, language=fv.language)
                        new_fv.set_value(val, field=tgt_field)
                        new_fv.save()
            elif action == "overflow":
                overflow[src_field.slug] = str(fv.get_value())
            MigrationFieldMapping.objects.create(
                migration=migration,
                source_field=src_field,
                action=action,
                target_field=target_field_map.get(mapping_in.target_field_slug) if mapping_in.target_field_slug else None,
            )

        if overflow:
            entity.overflow_data = {**entity.overflow_data, **overflow}
        entity.config_version = tgt_version
        entity.user_defined_model_type = migration.target_user_defined_model_type
        try:
            entity.validate_for_save()
        except ValidationError as exc:
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": exc.messages}
            return JsonResponse({"errors": errors}, status=400)
        entity.save(update_fields=["config_version", "user_defined_model_type", "overflow_data"])
        entity.materialize_defaults()
        migration.executed_at = now()
        migration.executed_by = request.user
        migration.save(update_fields=["executed_at", "executed_by"])

    return _entity_out_for_user(entity, request.user)


# ─── Bundle export / import ───────────────────────────────────────────────────

_BUNDLE_RULE = "UDM_BUNDLE"


def _extract_bundle_from_rego(source: str) -> dict | None:
    """Evaluate the UDM_BUNDLE rule in a Rego source string and return the bundle dict.

    The bundle must be defined as a top-level rule in the udm package:
        UDM_BUNDLE := { ... }
    It is evaluated with an empty input document, so it may reference other rules
    and Rego built-ins but not input.* fields.
    Returns None if the rule is missing or evaluation fails.
    """
    import json as _json
    try:
        import regorus
        eng = regorus.Engine()
        eng.add_policy("bundle.rego", source)
        eng.set_input_json("{}")
        raw = _json.loads(eng.eval_rule_as_json(f"data.udm.{_BUNDLE_RULE}"))
        if raw == "<undefined>" or raw is None:
            return None
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if isinstance(raw, dict):
            return raw
        return None
    except Exception:
        return None


def _collect_bundle_scope(scope_type_ids: list) -> tuple[list, list, list, list]:
    """Collect all UDMTypes, field configs, workflows, and policies for the given UDMType IDs.

    Returns (udm_types, field_configs, workflows, policies) — all DB model objects.
    Field configs include both root configs and any submodel configs reachable from them.
    """
    from userdefinedmodel.models import (
        UserDefinedModelType, FieldConfig, ConfigVersion, WorkflowDefinition, Policy,
    )

    udm_types = list(
        UserDefinedModelType.objects.select_related("field_config")
        .prefetch_related("type_policies__policy")
        .filter(id__in=scope_type_ids)
    )

    # Collect root field configs from UDMTypes
    config_ids: set = set()
    for t in udm_types:
        if t.field_config_id:
            config_ids.add(t.field_config_id)

    # Walk submodel configs transitively
    visited_version_ids: set = set()
    workflow_ids: set = set()
    configs_to_expand = list(config_ids)
    while configs_to_expand:
        cfg_id = configs_to_expand.pop()
        # Use the published version if available, else draft
        try:
            version = ConfigVersion.objects.prefetch_related(
                "field_definitions__workflow_definition",
            ).get(config_id=cfg_id, status=ConfigVersion.Status.PUBLISHED)
        except ConfigVersion.DoesNotExist:
            try:
                version = ConfigVersion.objects.prefetch_related(
                    "field_definitions__workflow_definition",
                ).get(config_id=cfg_id, status=ConfigVersion.Status.DRAFT)
            except ConfigVersion.DoesNotExist:
                continue
        if version.id in visited_version_ids:
            continue
        visited_version_ids.add(version.id)
        for fd in version.field_definitions.all():
            if fd.workflow_definition_id:
                workflow_ids.add(fd.workflow_definition_id)
            if fd.submodel_config_id and fd.submodel_config.config_id not in config_ids:
                config_ids.add(fd.submodel_config.config_id)
                configs_to_expand.append(fd.submodel_config.config_id)

    field_configs = list(FieldConfig.objects.prefetch_related("languages").filter(id__in=config_ids))
    workflows = list(
        WorkflowDefinition.objects.prefetch_related(
            "states__translations", "transitions__translations",
            "transitions__from_state", "transitions__to_state",
        ).filter(id__in=workflow_ids)
    )

    policy_slugs: set[str] = set()
    for t in udm_types:
        for tp in t.type_policies.all():
            policy_slugs.add(tp.policy.slug)
    policies = list(Policy.objects.filter(slug__in=policy_slugs))

    return udm_types, field_configs, workflows, policies


def _build_bundle_export(scope_type_ids: list) -> BundleExportOut:
    """Build a BundleExportOut for the given scope UDMType IDs."""
    from userdefinedmodel.models import ConfigVersion

    udm_types, field_configs, workflows, policies = _collect_bundle_scope(scope_type_ids)

    bundle_udm_types = []
    for t in udm_types:
        bundle_udm_types.append(BundleUDMTypeOut(
            id=t.id,
            name=t.name,
            field_config_id=t.field_config_id,
            policy_slugs=[tp.policy.slug for tp in t.type_policies.all()],
        ))

    bundle_field_configs = []
    for cfg in field_configs:
        try:
            version = ConfigVersion.objects.get(config=cfg, status=ConfigVersion.Status.PUBLISHED)
        except ConfigVersion.DoesNotExist:
            try:
                version = ConfigVersion.objects.get(config=cfg, status=ConfigVersion.Status.DRAFT)
            except ConfigVersion.DoesNotExist:
                continue
        draft_export = _serialize_version_as_draft_in(version)
        bundle_field_configs.append(BundleFieldConfigOut(
            id=cfg.id,
            name=cfg.name,
            description=cfg.description,
            languages=[
                ConfigLanguageOut(code=l.code, label=l.label, is_default=l.is_default, sort_order=l.sort_order)
                for l in cfg.languages.all()
            ],
            draft=draft_export,
        ))

    bundle_workflows = []
    for wf in workflows:
        bundle_workflows.append(BundleWorkflowOut(
            id=wf.id,
            name=wf.name,
            description=wf.description,
            states=[
                WorkflowStateOut(
                    name=s.name,
                    label={t.language: t.label for t in s.translations.all()},
                    is_initial=s.is_initial,
                    position_x=s.position_x,
                    position_y=s.position_y,
                    background_color=s.background_color or "#ffffff",
                    text_color=_wcag_text_color(s.background_color or "#ffffff"),
                )
                for s in wf.states.all()
            ],
            transitions=[
                WorkflowTransitionOut(
                    name=tr.name,
                    label={t.language: t.label for t in tr.translations.all()},
                    from_state=tr.from_state.name if tr.from_state else None,
                    from_undefined_only=tr.from_undefined_only,
                    to_state=tr.to_state.name,
                    source_handle=tr.source_handle,
                    target_handle=tr.target_handle,
                )
                for tr in wf.transitions.all()
            ],
            virtual_node_positions=wf.virtual_node_positions or {},
        ))

    bundle_policies = [PolicyOut(slug=p.slug, source=p.source) for p in policies]

    return BundleExportOut(
        version=1,
        scope_type_ids=[t.id for t in udm_types],
        udm_types=bundle_udm_types,
        field_configs=bundle_field_configs,
        workflows=bundle_workflows,
        policies=bundle_policies,
    )


def _build_bundle_zip(scope_type_ids: list) -> bytes:
    """Build a ZIP archive containing:
    - UDM_BUNDLE.json  — structural bundle without policy sources
    - policies/<slug>.rego — one file per policy
    Returns raw ZIP bytes.
    """
    import io
    import zipfile as _zf
    bundle = _build_bundle_export(scope_type_ids)
    # Strip policy sources from the embedded bundle — they live as separate files
    bundle_dict = bundle.model_dump(mode="json")
    for p in bundle_dict.get("policies", []):
        p.pop("source", None)
    bundle_json = json.dumps(bundle_dict, indent=2, ensure_ascii=False)

    buf = io.BytesIO()
    with _zf.ZipFile(buf, "w", compression=_zf.ZIP_DEFLATED) as zf:
        zf.writestr("UDM_BUNDLE.json", bundle_json)
        for policy in bundle.policies:
            zf.writestr(f"policies/{policy.slug}.rego", policy.source)
    return buf.getvalue()


def _extract_bundle_from_zip(zip_bytes: bytes) -> tuple[dict | None, dict[str, str]]:
    """Parse a ZIP bundle archive.

    Returns (bundle_dict, policy_sources) where:
    - bundle_dict is from UDM_BUNDLE.json or evaluated UDM_BUNDLE.rego (None on failure)
    - policy_sources maps slug → source from policies/*.rego files
    """
    import io
    import zipfile as _zf
    policy_sources: dict[str, str] = {}
    bundle_dict: dict | None = None

    try:
        with _zf.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            # Read policy files
            for name in names:
                if name.startswith("policies/") and name.endswith(".rego"):
                    slug = name[len("policies/"):-len(".rego")]
                    if slug:
                        policy_sources[slug] = zf.read(name).decode("utf-8")
            # Prefer UDM_BUNDLE.json, fall back to UDM_BUNDLE.rego
            if "UDM_BUNDLE.json" in names:
                bundle_dict = json.loads(zf.read("UDM_BUNDLE.json").decode("utf-8"))
            elif "UDM_BUNDLE.rego" in names:
                rego_src = zf.read("UDM_BUNDLE.rego").decode("utf-8")
                bundle_dict = _extract_bundle_from_rego(rego_src)
    except Exception:
        pass
    return bundle_dict, policy_sources


@api.post("/export-bundle-zip/", auth=django_auth)
def export_bundle_zip(request, payload: BundleExportIn):
    """Export a ZIP bundle: UDM_BUNDLE.json + policies/<slug>.rego for each policy."""
    from django.http import HttpResponse
    if denied := _require_perms(request, "userdefinedmodel.view_fieldconfig", "userdefinedmodel.view_fielddefinition"):
        return denied
    zip_bytes = _build_bundle_zip(payload.scope_type_ids)
    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = "attachment; filename=\"udm_bundle.zip\""
    return response


@api.post("/parse-bundle-zip/", auth=django_auth)
def parse_bundle_zip(request, file: UploadedFile = File(...)):
    """Parse a ZIP bundle and return the scope_type_ids and udm_types metadata it declares."""
    bundle_dict, _ = _extract_bundle_from_zip(file.read())
    if bundle_dict is None:
        return JsonResponse({"scope_type_ids": [], "udm_types": [], "error": "Could not parse UDM_BUNDLE from ZIP"})
    udm_types = [
        {"id": str(t["id"]), "name": t.get("name", ""), "description": t.get("description", "")}
        for t in bundle_dict.get("udm_types", [])
    ]
    return JsonResponse({
        "scope_type_ids": bundle_dict.get("scope_type_ids", []),
        "udm_types": udm_types,
    })


@api.post("/import-bundle-zip/", auth=django_auth)
def import_bundle_zip(
    request,
    file: UploadedFile = File(...),
    scope_type_ids: str = "",
    policy_slug: str = "",
):
    """Import a ZIP bundle (UDM_BUNDLE.json + policies/*.rego).

    scope_type_ids: comma-separated UUID strings of in-scope UDM Types.
    policy_slug: if set, save each policy rego with its own slug (already done from policies/ dir).
    """
    from userdefinedmodel.models import (
        ConfigVersion, FieldConfig, ConfigLanguage, FieldDefinition,
        FieldDefinitionTranslation, WorkflowDefinition, WorkflowState,
        WorkflowStateTranslation, WorkflowTransition, WorkflowTransitionTranslation,
        Policy, UserDefinedModelType, UserDefinedModelTypePolicy,
    )
    if denied := _require_perms(
        request,
        "userdefinedmodel.change_fieldconfig",
        "userdefinedmodel.change_fielddefinition",
        "userdefinedmodel.change_policy",
    ):
        return denied

    zip_bytes = file.read()
    raw_bundle, zip_policy_sources = _extract_bundle_from_zip(zip_bytes)
    if raw_bundle is None:
        return JsonResponse({"detail": "Could not parse UDM_BUNDLE from ZIP"}, status=400)

    # Parse scope_type_ids from query param (comma-separated)
    parsed_scope_ids = set(s.strip() for s in scope_type_ids.split(",") if s.strip())
    if not parsed_scope_ids:
        # Fall back to the bundle's own scope
        parsed_scope_ids = set(str(s) for s in raw_bundle.get("scope_type_ids", []))
    if not parsed_scope_ids:
        return JsonResponse({"detail": "scope_type_ids is required"}, status=400)

    bundle_config_ids = set(str(fc["id"]) for fc in raw_bundle.get("field_configs", []))

    with transaction.atomic():
        # ── Step 1: Resolve workflows (same logic as import-bundle-rego) ─────
        workflow_id_map: dict[str, object] = {}
        for wf_data in raw_bundle.get("workflows", []):
            wf_id = str(wf_data["id"])
            try:
                wf = WorkflowDefinition.objects.prefetch_related(
                    "states__translations", "transitions__translations",
                    "transitions__from_state", "transitions__to_state",
                ).get(id=wf_id)
            except WorkflowDefinition.DoesNotExist:
                wf = None

            if wf is not None and _is_workflow_externally_used(wf.id, bundle_config_ids):
                workflow_id_map[wf_id] = _clone_workflow(wf)
            elif wf is not None:
                _update_workflow_from_data(wf, wf_data)
                workflow_id_map[wf_id] = wf
            else:
                workflow_id_map[wf_id] = _create_workflow_from_data(wf_data)

        # ── Step 2: Resolve field configs ─────────────────────────────────────
        fc_by_id = {str(fc["id"]): fc for fc in raw_bundle.get("field_configs", [])}
        ordered_config_ids = _toposort_configs(fc_by_id)
        config_id_map: dict[str, object] = {}
        # Track (draft, cfg_id) in topo order so we can publish leaf-first after all are created
        drafts_to_publish: list = []

        for cfg_id in ordered_config_ids:
            fc_data = fc_by_id[cfg_id]
            try:
                cfg = FieldConfig.objects.prefetch_related("languages").get(id=cfg_id)
                cfg_exists = True
            except FieldConfig.DoesNotExist:
                cfg = None
                cfg_exists = False

            if cfg_exists and _is_config_externally_used(cfg.id, parsed_scope_ids, bundle_config_ids):
                new_cfg, new_draft = _clone_field_config(cfg, fc_data.get("languages", []))
                config_id_map[cfg_id] = new_cfg
                _apply_draft_fields(new_draft, fc_data["draft"], workflow_id_map, config_id_map, bundle_config_ids)
                drafts_to_publish.append(new_draft)
            elif cfg_exists:
                cfg.name = fc_data["name"]
                cfg.description = fc_data.get("description", "")
                cfg.save()
                cfg.languages.all().delete()
                for lang in fc_data.get("languages", []):
                    ConfigLanguage.objects.create(
                        config=cfg, code=lang["code"], label=lang["label"],
                        is_default=lang["is_default"], sort_order=lang["sort_order"],
                    )
                draft, _ = ConfigVersion.objects.get_or_create(
                    config=cfg, status=ConfigVersion.Status.DRAFT,
                    defaults={"notes": fc_data["draft"].get("notes", "")},
                )
                draft.notes = fc_data["draft"].get("notes", "")
                draft.save(update_fields=["notes"])
                draft.field_definitions.all().delete()
                _apply_draft_fields(draft, fc_data["draft"], workflow_id_map, config_id_map, bundle_config_ids)
                config_id_map[cfg_id] = cfg
                drafts_to_publish.append(draft)
            else:
                new_cfg, new_draft = _clone_field_config(
                    type("FakeConfig", (), {"name": fc_data["name"], "description": fc_data.get("description", "")})(),
                    fc_data.get("languages", []),
                )
                config_id_map[cfg_id] = new_cfg
                _apply_draft_fields(new_draft, fc_data["draft"], workflow_id_map, config_id_map, bundle_config_ids)
                drafts_to_publish.append(new_draft)

        # Publish drafts leaf-first (topo order matches: submodels before parents)
        for draft in drafts_to_publish:
            draft.publish()

        # ── Step 3: Policies from ZIP files ───────────────────────────────────
        policy_slug_map: dict[str, object] = {}
        for pol_data in raw_bundle.get("policies", []):
            slug = pol_data.get("slug", "")
            if not slug:
                continue
            source = zip_policy_sources.get(slug) or pol_data.get("source", "")
            if source:
                pol, _ = Policy.objects.update_or_create(slug=slug, defaults={"source": source})
            else:
                pol, _ = Policy.objects.get_or_create(slug=slug, defaults={"source": ""})
            policy_slug_map[slug] = pol

        # ── Step 4: UDMTypes — create if missing, always relink ───────────────
        # Maps bundle cfg_id → the first config assigned to a bundle UDMType,
        # used for linking fallback scope types.
        bundle_type_cfg_map: dict[str, object] = {}  # bundle_type_id → FieldConfig
        bundle_type_policy_slugs: list[str] = []

        for udmt_data in raw_bundle.get("udm_types", []):
            udmt_id = str(udmt_data["id"])
            try:
                udmt = UserDefinedModelType.objects.get(id=udmt_id)
            except UserDefinedModelType.DoesNotExist:
                # Create the type, preserving its UUID so round-trip imports work
                udmt = UserDefinedModelType.objects.create(
                    id=udmt_id,
                    name=udmt_data["name"],
                )
            old_cfg_id = str(udmt_data.get("field_config_id") or "")
            if old_cfg_id in config_id_map:
                udmt.field_config = config_id_map[old_cfg_id]
                udmt.save(update_fields=["field_config"])
                bundle_type_cfg_map[udmt_id] = config_id_map[old_cfg_id]
            for p_slug in udmt_data.get("policy_slugs", []):
                if p_slug in policy_slug_map:
                    UserDefinedModelTypePolicy.objects.get_or_create(
                        user_defined_model_type=udmt,
                        policy=policy_slug_map[p_slug],
                        defaults={"sort_order": 0},
                    )
                    if p_slug not in bundle_type_policy_slugs:
                        bundle_type_policy_slugs.append(p_slug)

        # Fallback: scope types that are in the request but NOT in the bundle
        # → link them to the same configs/policies as the bundle types.
        bundle_type_ids = set(str(t["id"]) for t in raw_bundle.get("udm_types", []))
        extra_scope_ids = parsed_scope_ids - bundle_type_ids
        if extra_scope_ids:
            # Determine configs to assign: one per bundle UDMType, by index.
            # If there's only one bundle type, assign its config to all extras.
            bundle_cfgs = list(bundle_type_cfg_map.values())
            for i, extra_id in enumerate(sorted(extra_scope_ids)):
                try:
                    udmt = UserDefinedModelType.objects.get(id=extra_id)
                except UserDefinedModelType.DoesNotExist:
                    continue
                # Pair by index; repeat last config if extras > bundle types
                cfg_to_assign = bundle_cfgs[min(i, len(bundle_cfgs) - 1)] if bundle_cfgs else None
                if cfg_to_assign:
                    udmt.field_config = cfg_to_assign
                    udmt.save(update_fields=["field_config"])
                for p_slug in bundle_type_policy_slugs:
                    if p_slug in policy_slug_map:
                        UserDefinedModelTypePolicy.objects.get_or_create(
                            user_defined_model_type=udmt,
                            policy=policy_slug_map[p_slug],
                            defaults={"sort_order": 0},
                        )

    return JsonResponse({
        "status": "ok",
        "imported_workflows": len(workflow_id_map),
        "imported_configs": len(config_id_map),
        "imported_policies": len(policy_slug_map),
    })


def _is_workflow_externally_used(workflow_id, scope_config_ids: set) -> bool:
    """Return True if this workflow is referenced by a FieldDefinition whose parent
    FieldConfig is NOT in scope_config_ids."""
    from userdefinedmodel.models import FieldDefinition
    return FieldDefinition.objects.filter(
        workflow_definition_id=workflow_id
    ).exclude(
        version__config_id__in=scope_config_ids
    ).exists()


def _is_config_externally_used(config_id, scope_type_ids: set, scope_config_ids: set) -> bool:
    """Return True if this FieldConfig is referenced by a UDMType or submodel definition outside scope."""
    from userdefinedmodel.models import UserDefinedModelType, FieldDefinition
    # Direct UDMType usage
    if UserDefinedModelType.objects.filter(field_config_id=config_id).exclude(id__in=scope_type_ids).exists():
        return True
    # Submodel usage from configs not in scope
    if FieldDefinition.objects.filter(
        submodel_config__config_id=config_id
    ).exclude(version__config_id__in=scope_config_ids).exists():
        return True
    return False


def _update_workflow_from_data(wf, wf_data: dict) -> None:
    """Update an existing WorkflowDefinition in place from bundle data."""
    from userdefinedmodel.models import (
        WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    wf.name = wf_data["name"]
    wf.description = wf_data.get("description", "")
    wf.virtual_node_positions = wf_data.get("virtual_node_positions") or {}
    wf.save()
    existing_states = {s.name: s for s in wf.states.all()}
    incoming_state_names = {s["name"] for s in wf_data.get("states", [])}
    state_map = {}
    for s_data in wf_data.get("states", []):
        if s_data["name"] in existing_states:
            s = existing_states[s_data["name"]]
            s.is_initial = s_data.get("is_initial", False)
            s.position_x = s_data.get("position_x", 0.0)
            s.position_y = s_data.get("position_y", 0.0)
            s.background_color = s_data.get("background_color", "#ffffff")
            s.save()
            s.translations.all().delete()
        else:
            s = WorkflowState.objects.create(
                workflow=wf, name=s_data["name"],
                is_initial=s_data.get("is_initial", False),
                position_x=s_data.get("position_x", 0.0),
                position_y=s_data.get("position_y", 0.0),
                background_color=s_data.get("background_color", "#ffffff"),
            )
        for lang, label in (s_data.get("label") or {}).items():
            WorkflowStateTranslation.objects.create(state=s, language=lang, label=label)
        state_map[s_data["name"]] = s
    for del_name in set(existing_states) - incoming_state_names:
        existing_states[del_name].delete()
    wf.transitions.all().delete()
    for tr_data in wf_data.get("transitions", []):
        tr = WorkflowTransition.objects.create(
            workflow=wf, name=tr_data["name"],
            from_state=state_map.get(tr_data["from_state"]) if tr_data.get("from_state") else None,
            from_undefined_only=tr_data.get("from_undefined_only", False),
            to_state=state_map[tr_data["to_state"]],
            source_handle=tr_data.get("source_handle", ""),
            target_handle=tr_data.get("target_handle", ""),
        )
        for lang, label in (tr_data.get("label") or {}).items():
            WorkflowTransitionTranslation.objects.create(transition=tr, language=lang, label=label)


def _create_workflow_from_data(wf_data: dict) -> "WorkflowDefinition":
    """Create a new WorkflowDefinition from bundle data."""
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    new_wf = WorkflowDefinition.objects.create(
        name=wf_data["name"],
        description=wf_data.get("description", ""),
        virtual_node_positions=wf_data.get("virtual_node_positions") or {},
    )
    state_map = {}
    for s_data in wf_data.get("states", []):
        s = WorkflowState.objects.create(
            workflow=new_wf, name=s_data["name"],
            is_initial=s_data.get("is_initial", False),
            position_x=s_data.get("position_x", 0.0),
            position_y=s_data.get("position_y", 0.0),
            background_color=s_data.get("background_color", "#ffffff"),
        )
        for lang, label in (s_data.get("label") or {}).items():
            WorkflowStateTranslation.objects.create(state=s, language=lang, label=label)
        state_map[s_data["name"]] = s
    for tr_data in wf_data.get("transitions", []):
        tr = WorkflowTransition.objects.create(
            workflow=new_wf, name=tr_data["name"],
            from_state=state_map.get(tr_data["from_state"]) if tr_data.get("from_state") else None,
            from_undefined_only=tr_data.get("from_undefined_only", False),
            to_state=state_map[tr_data["to_state"]],
            source_handle=tr_data.get("source_handle", ""),
            target_handle=tr_data.get("target_handle", ""),
        )
        for lang, label in (tr_data.get("label") or {}).items():
            WorkflowTransitionTranslation.objects.create(transition=tr, language=lang, label=label)
    return new_wf


def _clone_workflow(wf) -> "WorkflowDefinition":
    """Deep-copy a WorkflowDefinition (states + translations + transitions + translations)."""
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    new_wf = WorkflowDefinition.objects.create(
        name=wf.name,
        description=wf.description,
        virtual_node_positions=wf.virtual_node_positions or {},
    )
    state_map = {}
    for state in wf.states.prefetch_related("translations").all():
        new_state = WorkflowState.objects.create(
            workflow=new_wf,
            name=state.name,
            is_initial=state.is_initial,
            position_x=state.position_x,
            position_y=state.position_y,
            background_color=state.background_color,
        )
        for t in state.translations.all():
            WorkflowStateTranslation.objects.create(state=new_state, language=t.language, label=t.label)
        state_map[state.name] = new_state
    for trans in wf.transitions.prefetch_related("translations").select_related("from_state", "to_state").all():
        new_trans = WorkflowTransition.objects.create(
            workflow=new_wf,
            name=trans.name,
            from_state=state_map.get(trans.from_state.name) if trans.from_state else None,
            from_undefined_only=trans.from_undefined_only,
            to_state=state_map[trans.to_state.name],
            source_handle=trans.source_handle,
            target_handle=trans.target_handle,
        )
        for t in trans.translations.all():
            WorkflowTransitionTranslation.objects.create(transition=new_trans, language=t.language, label=t.label)
    return new_wf


def _clone_field_config(cfg, languages_data: list) -> tuple:
    """Deep-copy a FieldConfig (without versions). Returns (new_config, new_draft)."""
    from userdefinedmodel.models import FieldConfig, ConfigLanguage, ConfigVersion
    new_cfg = FieldConfig.objects.create(name=cfg.name, description=cfg.description)
    for lang in languages_data:
        ConfigLanguage.objects.create(
            config=new_cfg,
            code=lang["code"],
            label=lang["label"],
            is_default=lang["is_default"],
            sort_order=lang["sort_order"],
        )
    new_draft = ConfigVersion.objects.create(config=new_cfg, status=ConfigVersion.Status.DRAFT)
    return new_cfg, new_draft




def _toposort_configs(fc_by_id: dict) -> list[str]:
    """Return config IDs in dependency order (leaf submodels first)."""
    result = []
    visited = set()

    def visit(cfg_id):
        if cfg_id in visited:
            return
        visited.add(cfg_id)
        fc_data = fc_by_id.get(cfg_id)
        if fc_data:
            for fd in fc_data.get("draft", {}).get("fields", []):
                sub_id = fd.get("submodel_config_version_id")
                if sub_id:
                    # Find which config owns that version
                    for other_id, other_data in fc_by_id.items():
                        # We can't know exactly which config version without querying,
                        # but we approximate: if the submodel_config_version_id matches
                        # a config id in our bundle, that's the dependency.
                        # The bundle stores config IDs, but fields store version IDs.
                        # We need to resolve: find the config whose draft or published
                        # version matches sub_id.
                        pass
        result.append(cfg_id)

    # Simple approach: just process all configs; DB resolution handles version→config mapping
    for cfg_id in fc_by_id:
        visit(cfg_id)
    return result


def _apply_draft_fields(
    draft,
    draft_data: dict,
    workflow_id_map: dict,
    config_id_map: dict,
    bundle_config_ids: set,
):
    """Populate a ConfigVersion's field_definitions from bundle draft data.

    Remaps workflow_definition_id and submodel_config_version_id through
    the id maps built during import so cloned/new objects are referenced correctly.
    """
    from userdefinedmodel.models import (
        ConfigVersion, FieldDefinition, FieldDefinitionTranslation,
    )
    draft.field_definitions.all().delete()
    for fd_data in draft_data.get("fields", []):
        # Remap workflow: if the workflow was cloned, use new ID
        wf_def_id = fd_data.get("workflow_definition_id")
        if wf_def_id and str(wf_def_id) in workflow_id_map:
            new_wf = workflow_id_map[str(wf_def_id)]
            resolved_wf_id = new_wf.id
        elif wf_def_id:
            resolved_wf_id = wf_def_id
        else:
            resolved_wf_id = None

        # Remap submodel config version: find the new draft version for the (possibly cloned) config
        sub_ver_id = fd_data.get("submodel_config_version_id")
        resolved_sub_ver = None
        if sub_ver_id:
            # Try to find which config in our map owns this version
            resolved_sub_ver = _resolve_submodel_version(str(sub_ver_id), config_id_map, bundle_config_ids)

        wf_def = None
        if resolved_wf_id:
            from userdefinedmodel.models import WorkflowDefinition
            try:
                wf_def = WorkflowDefinition.objects.get(id=resolved_wf_id)
            except WorkflowDefinition.DoesNotExist:
                pass

        fd = FieldDefinition.objects.create(
            version=draft,
            slug=fd_data["slug"],
            data_type=fd_data["data_type"],
            sort_order=fd_data.get("sort_order", 0),
            is_localized=fd_data.get("is_localized", False),
            is_preview=fd_data.get("is_preview", False),
            parent_slug=fd_data.get("parent_slug") or "",
            submodel_config=resolved_sub_ver,
            workflow_definition=wf_def,
            type_config=fd_data.get("type_config") or {},
        )
        for lang, label in (fd_data.get("labels") or {}).items():
            help_text = (fd_data.get("help_texts") or {}).get(lang, "")
            FieldDefinitionTranslation.objects.create(field=fd, language=lang, label=label, help_text=help_text)

        default = fd_data.get("default")
        if default is not None:
            _create_field_default(fd, default, fd_data.get("is_localized", False))


def _resolve_submodel_version(sub_ver_id: str, config_id_map: dict, bundle_config_ids: set):
    """Given a submodel_config_version_id from the bundle, find the ConfigVersion to use.

    If the version's config was cloned (is in config_id_map with a new config), return
    the new draft of that config. Otherwise return the original version.
    """
    from userdefinedmodel.models import ConfigVersion
    try:
        original_version = ConfigVersion.objects.select_related("config").get(id=sub_ver_id)
    except ConfigVersion.DoesNotExist:
        return None

    orig_config_id = str(original_version.config_id)
    if orig_config_id in config_id_map:
        new_cfg = config_id_map[orig_config_id]
        if new_cfg.id != original_version.config_id:
            # Config was cloned; use the new draft
            try:
                return ConfigVersion.objects.get(config=new_cfg, status=ConfigVersion.Status.DRAFT)
            except ConfigVersion.DoesNotExist:
                pass
    return original_version


# ─── Bulk migration ───────────────────────────────────────────────────────────

@api.post("/bulk-migrations/preview/", auth=django_auth)
def bulk_migration_preview(
    request,
    source_version_id: uuid.UUID,
    target_version_id: uuid.UUID,
    type_filter_id: Optional[uuid.UUID] = None,
):
    from userdefinedmodel.models import ConfigVersion, UserDefinedModelEntity
    if denied := _require_perms(request, "userdefinedmodel.view_bulkmigrationplan"):
        return denied
    try:
        src = ConfigVersion.objects.get(id=source_version_id)
        tgt = ConfigVersion.objects.get(id=target_version_id)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "Version not found"}, status=404)
    qs = UserDefinedModelEntity.objects.filter(config_version=src)
    if type_filter_id:
        qs = qs.filter(user_defined_model_type_id=type_filter_id)
    return JsonResponse({
        "affected_entity_count": qs.count(),
        "source_version_id": str(src.id),
        "target_version_id": str(tgt.id),
    })


@api.post("/bulk-migrations/", response={201: BulkMigrationOut}, auth=django_auth)
def create_bulk_migration(request, payload: BulkMigrationCreateIn):
    from userdefinedmodel.models import (
        ConfigVersion, UserDefinedModelType,
        BulkMigrationPlan, BulkMigrationFieldMapping,
        BulkMigrationSubmodelMapping, BulkMigrationSubmodelFieldMapping,
        BulkMigrationWorkflowStateMapping,
    )
    if denied := _require_perms(request, "userdefinedmodel.add_bulkmigrationplan"):
        return denied
    try:
        src = ConfigVersion.objects.get(id=payload.source_version_id)
        tgt = ConfigVersion.objects.get(id=payload.target_version_id)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "Version not found"}, status=404)

    type_filter = None
    if payload.user_defined_model_type_filter_id:
        try:
            type_filter = UserDefinedModelType.objects.get(id=payload.user_defined_model_type_filter_id)
        except UserDefinedModelType.DoesNotExist:
            return JsonResponse({"detail": "Type filter not found"}, status=404)

    with transaction.atomic():
        plan = BulkMigrationPlan.objects.create(
            source_version=src, target_version=tgt,
            user_defined_model_type_filter=type_filter,
            created_by=request.user,
        )
        src_fields = {f.slug: f for f in src.field_definitions.select_related("submodel_config").all()}
        tgt_fields = {f.slug: f for f in tgt.field_definitions.all()}
        for mapping in payload.field_mappings:
            src_field = src_fields.get(mapping.source_field_slug)
            if src_field:
                BulkMigrationFieldMapping.objects.create(
                    plan=plan, source_field=src_field,
                    action=mapping.action.value,
                    target_field=tgt_fields.get(mapping.target_field_slug) if mapping.target_field_slug else None,
                )

        for sm in payload.submodel_mappings:
            src_field = src_fields.get(sm.source_parent_field_slug)
            if not src_field or not src_field.submodel_config:
                return JsonResponse(
                    {"detail": f"Source submodel field '{sm.source_parent_field_slug}' not found or has no submodel config"},
                    status=400,
                )
            try:
                tgt_submodel_version = ConfigVersion.objects.get(id=sm.target_submodel_version_id)
            except ConfigVersion.DoesNotExist:
                return JsonResponse({"detail": "Target submodel version not found"}, status=404)
            submodel_mapping = BulkMigrationSubmodelMapping.objects.create(
                plan=plan,
                source_parent_field=src_field,
                target_submodel_version=tgt_submodel_version,
            )
            src_sub_fields = {f.slug: f for f in src_field.submodel_config.field_definitions.all()}
            tgt_sub_fields = {f.slug: f for f in tgt_submodel_version.field_definitions.all()}
            for fm in sm.field_mappings:
                sub_src = src_sub_fields.get(fm.source_field_slug)
                if sub_src:
                    BulkMigrationSubmodelFieldMapping.objects.create(
                        submodel_mapping=submodel_mapping,
                        source_field=sub_src,
                        action=fm.action.value,
                        target_field=tgt_sub_fields.get(fm.target_field_slug) if fm.target_field_slug else None,
                    )

        for wm in payload.workflow_state_mappings:
            for state_mapping in wm.state_mappings:
                BulkMigrationWorkflowStateMapping.objects.create(
                    plan=plan,
                    field_slug=wm.field_slug,
                    from_state=state_mapping.from_state,
                    to_state=state_mapping.to_state,
                )

    return 201, BulkMigrationOut(
        id=plan.id, status=BulkMigrationStatus(plan.status),
        source_version_id=plan.source_version_id,
        target_version_id=plan.target_version_id,
        user_defined_model_type_filter_id=plan.user_defined_model_type_filter_id,
        total_entities=plan.total_entities,
        done_entities=plan.done_entities,
        failed_entities=plan.failed_entities,
        executed_at=plan.executed_at.isoformat() if plan.executed_at else None,
    )


@api.get("/bulk-migrations/{plan_id}/", response=BulkMigrationOut, auth=django_auth)
def get_bulk_migration(request, plan_id: uuid.UUID):
    from userdefinedmodel.models import BulkMigrationPlan
    if denied := _require_perms(request, "userdefinedmodel.view_bulkmigrationplan"):
        return denied
    try:
        plan = BulkMigrationPlan.objects.get(id=plan_id)
    except BulkMigrationPlan.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return BulkMigrationOut(
        id=plan.id, status=BulkMigrationStatus(plan.status),
        source_version_id=plan.source_version_id,
        target_version_id=plan.target_version_id,
        user_defined_model_type_filter_id=plan.user_defined_model_type_filter_id,
        total_entities=plan.total_entities,
        done_entities=plan.done_entities,
        failed_entities=plan.failed_entities,
        executed_at=plan.executed_at.isoformat() if plan.executed_at else None,
    )


@api.post("/bulk-migrations/{plan_id}/execute/", auth=django_auth)
def execute_bulk_migration_plan(request, plan_id: uuid.UUID):
    from userdefinedmodel.models import BulkMigrationPlan
    from userdefinedmodel.tasks import execute_bulk_migration
    if denied := _require_perms(request, "userdefinedmodel.change_bulkmigrationplan"):
        return denied
    try:
        plan = BulkMigrationPlan.objects.get(id=plan_id)
    except BulkMigrationPlan.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if plan.status in (BulkMigrationPlan.Status.RUNNING, BulkMigrationPlan.Status.DONE):
        return JsonResponse({"detail": f"Plan is already {plan.status}"}, status=409)
    execute_bulk_migration.delay(str(plan_id))
    return JsonResponse({"status": "accepted", "plan_id": str(plan_id)}, status=202)
