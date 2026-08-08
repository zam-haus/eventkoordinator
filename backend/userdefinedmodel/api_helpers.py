"""Shared helpers used across multiple userdefinedmodel API route modules."""
from __future__ import annotations

import logging

from django.http import JsonResponse

from userdefinedmodel.schemas import (
    ConfigDraftExportOut,
    ConfigLanguageOut,
    ConfigVersionOut,
    EntityOut,
    FieldDefinitionDraftOut,
    FieldDefinitionOut,
    WorkflowDefinitionOut,
    WorkflowStateOut,
    WorkflowTransitionOut,
    WorkflowVersionOut,
)

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Error response raised from inside a route handler.

    Raising (instead of returning a JsonResponse) matters inside
    transaction.atomic() blocks: an exception aborts the transaction, whereas a
    normal return would commit any writes made before the error was detected.
    Converted to a JsonResponse by the NinjaAPI exception handler in api.py.
    """

    def __init__(self, status: int, payload: dict):
        super().__init__(payload)
        self.status = status
        self.payload = payload


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


def _locale(request) -> str | None:
    """The requesting user's locale for the policy input. None only for system
    self-calls (background tasks) — HTTP requests always carry one."""
    return getattr(request, "LANGUAGE_CODE", None) or "en"


def _policy_allows(entity, user, action: str, **kwargs) -> bool:
    """Object-level authorization for an entity action via its Rego policy.

    Default-deny: an entity with no UDMType or no attached policies, and any action
    not affirmatively granted by a policy clause, evaluates to False.
    """
    from userdefinedmodel.engine import evaluate_policy
    return evaluate_policy(entity, user, action, **kwargs).allow


def _entity_out_for_user(entity, user, policy_messages: list | None = None, view_policy=None, locale=None) -> EntityOut:
    from userdefinedmodel.writer import serialize_node
    from userdefinedmodel.engine import evaluate_policy
    from userdefinedmodel.display_templates import render_markdown_displays_for_entity
    policy = view_policy if view_policy is not None else evaluate_policy(entity, user, "view", locale=locale)
    # Per-node grants (§5): serialize_node is the single redaction point and
    # filters the WHOLE tree recursively — root and submodels alike, also when
    # `entity` is itself a submodel node. Deny-by-default: nodes absent from
    # the map expose nothing.
    data = serialize_node(entity, viewable=policy.viewable_fields)
    data["viewable_fields"] = policy.viewable_fields
    data["editable_fields"] = policy.editable_fields
    data["deletable_nodes"] = policy.deletable_nodes
    data["creatable_submodels"] = policy.creatable_submodels
    data["policy_messages"] = policy_messages or []
    data["dashboard_columns"] = policy.dashboard_columns
    data["markdown_displays"] = render_markdown_displays_for_entity(entity.config_version, policy)
    return EntityOut(**data)


def _serialize_workflow_version(version) -> WorkflowVersionOut:
    """Serialize a WorkflowVersion's states/transitions."""
    states = []
    for state in version.states.prefetch_related("translations").all():
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
    for trans in version.transitions.prefetch_related("translations").select_related("from_state", "to_state").all():
        label_dict = {t.language: t.label for t in trans.translations.all()}
        transitions.append(WorkflowTransitionOut(
            name=trans.name, label=label_dict,
            from_state=trans.from_state.name if trans.from_state else None,
            from_undefined_only=trans.from_undefined_only,
            to_state=trans.to_state.name,
            source_handle=trans.source_handle,
            target_handle=trans.target_handle,
            properties=trans.properties or {},
        ))
    return WorkflowVersionOut(
        id=version.id,
        status=version.status,
        states=states,
        transitions=transitions,
        virtual_node_positions=version.virtual_node_positions or {},
    )


def _serialize_workflow(wf_def, version) -> WorkflowDefinitionOut:
    """Serialize a WorkflowDefinition with a specific version's content."""
    wf_ver = _serialize_workflow_version(version)
    initial = next((s for s in wf_ver.states if s.is_initial), None)
    draft_version_id = None
    published_version_id = None
    last_edited_at = None
    last_published_at = None
    for ver in wf_def.versions.all():
        if ver.status == "draft":
            draft_version_id = ver.id
            last_edited_at = ver.updated_at
        elif ver.status == "published":
            published_version_id = ver.id
            last_published_at = ver.published_at
    return WorkflowDefinitionOut(
        id=wf_def.id,
        name=wf_def.name,
        description=wf_def.description,
        initial_state=initial.name if initial else None,
        states=wf_ver.states,
        transitions=wf_ver.transitions,
        virtual_node_positions=wf_ver.virtual_node_positions,
        draft_version_id=draft_version_id,
        published_version_id=published_version_id,
        created_at=wf_def.created_at,
        last_edited_at=last_edited_at,
        last_published_at=last_published_at,
    )


def _serialize_config_version(version) -> ConfigVersionOut:
    from userdefinedmodel.models import FormElement, FormElementTranslation, FormElementBinding
    from userdefinedmodel.schemas import FormElementOut, FormElementBindingOut

    data_fields_out = []
    # Map data field slug -> FieldDefinitionOut for the backward-compat merge.
    df_out_by_slug: dict[str, FieldDefinitionOut] = {}
    for fd in version.field_definitions.prefetch_related(
        "defaults",
        "workflow_version__workflow__versions",
        "workflow_version__states__translations",
        "workflow_version__transitions__translations",
        "workflow_version__transitions__from_state",
        "workflow_version__transitions__to_state",
    ).all():
        defaults_qs = list(fd.defaults.all())
        default_val = None
        if defaults_qs:
            if fd.is_localized:
                default_val = {d.language: d.get_value(field=fd) for d in defaults_qs}
            else:
                default_val = defaults_qs[0].get_value(field=fd)

        workflow_ver_out = _serialize_workflow_version(fd.workflow_version) if fd.workflow_version else None

        out = FieldDefinitionOut(
            id=fd.id,
            slug=fd.slug,
            data_type=fd.data_type,
            is_localized=fd.is_localized,
            type_config=fd.type_config or {},
            default=default_val,
            submodel_config=_serialize_config_version(fd.submodel_config) if fd.submodel_config else None,
            workflow_version=workflow_ver_out,
        )
        data_fields_out.append(out)
        df_out_by_slug[fd.slug] = out

    # Form elements (tree + widgets) with translations + bindings.
    form_elements_out = []
    # Build a slug -> FormElementOut map for parent resolution + backward-compat.
    el_out_by_slug: dict[str, FormElementOut] = {}
    elements = list(version.form_elements.prefetch_related(
        "translations", "bindings__data_field",
    ).order_by("sort_order", "id").all())
    for el in elements:
        label_dict = {t.language: t.label for t in el.translations.all()}
        help_dict = {t.language: t.help_text for t in el.translations.all()}
        bindings_out = [
            FormElementBindingOut(data_field_slug=b.data_field.slug, role=b.role)
            for b in el.bindings.all()
        ]
        el_out = FormElementOut(
            id=el.id,
            slug=el.slug,
            element_type=el.element_type,
            parent_slug=el.parent.slug if el.parent_id else None,
            sort_order=el.sort_order,
            is_preview=el.is_preview,
            label=label_dict,
            help_text=help_dict,
            type_config=el.type_config or {},
            bindings=bindings_out,
        )
        form_elements_out.append(el_out)
        el_out_by_slug[el.slug] = el_out

    # Backward-compat `fields` merge: emit one entry per form element in the old
    # FieldDefinitionOut shape. Structural elements appear as their own entries
    # (data_type = element_type); 'field' elements appear as their bound data
    # field (with tree info + labels lifted from the element).
    fields_compat = []
    for el_out in form_elements_out:
        if el_out.element_type == "field" and el_out.bindings:
            df = df_out_by_slug.get(el_out.bindings[0].data_field_slug)
            if df is None:
                continue
            fields_compat.append(FieldDefinitionOut(
                id=df.id, slug=df.slug, data_type=df.data_type,
                is_localized=df.is_localized, type_config=df.type_config,
                submodel_config=df.submodel_config, workflow_version=df.workflow_version,
                default=df.default,
                sort_order=el_out.sort_order, is_preview=el_out.is_preview,
                label=el_out.label, help_text=el_out.help_text,
                parent_slug=el_out.parent_slug,
            ))
        else:
            # Structural element: emit as a pseudo data field with its element_type.
            # For multi-field widgets (e.g. date_range), fold the binding→data-field
            # slugs into type_config so the legacy entity editor (which iterates
            # `fields`) can render the paired editor with the right values.
            tc = dict(el_out.type_config or {})
            if el_out.bindings:
                tc["bindings"] = [
                    {"role": b.role, "data_field_slug": b.data_field_slug}
                    for b in el_out.bindings
                ]
            fields_compat.append(FieldDefinitionOut(
                id=el_out.id, slug=el_out.slug, data_type=el_out.element_type,
                is_localized=False, type_config=tc,
                sort_order=el_out.sort_order, is_preview=el_out.is_preview,
                label=el_out.label, help_text=el_out.help_text,
                parent_slug=el_out.parent_slug,
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
        data_fields=data_fields_out,
        form_elements=form_elements_out,
        fields=fields_compat,
    )


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


def _serialize_version_as_draft_in(version, bundle_config_ids=None) -> ConfigDraftExportOut:
    """Serialize a ConfigVersion into the shape that ConfigDraftIn / PUT draft accepts.

    Uses id references (submodel_config_version_id, workflow_definition_id) rather than
    nested objects, so the result can be fed directly back into replace_draft().

    bundle_config_ids: when provided, submodel fields whose config is in this set will
    export the FieldConfig UUID instead of the ConfigVersion UUID, so the importer can
    correctly re-link to the newly published version after import.
    """
    from userdefinedmodel.schemas import FormElementDraftOut, FormElementBindingDraftOut

    data_fields_out = []
    for fd in version.field_definitions.select_related(
        "workflow_version__workflow", "submodel_config__config"
    ).prefetch_related("defaults").all():
        defaults_qs = list(fd.defaults.all())
        default_val = None
        if defaults_qs:
            if fd.is_localized:
                default_val = {d.language: d.get_value(field=fd) for d in defaults_qs}
            else:
                default_val = defaults_qs[0].get_value(field=fd)

        # For in-bundle submodel configs, export the FieldConfig UUID so the importer
        # can defer resolution until after all configs are published (avoiding stale refs).
        sub_id = fd.submodel_config_id
        if (
            sub_id is not None
            and bundle_config_ids is not None
            and fd.submodel_config is not None
            and str(fd.submodel_config.config_id) in bundle_config_ids
        ):
            sub_id = fd.submodel_config.config_id

        wf_def_id = fd.workflow_version.workflow_id if fd.workflow_version_id else None
        data_fields_out.append(FieldDefinitionDraftOut(
            slug=fd.slug,
            data_type=fd.data_type,
            is_localized=fd.is_localized,
            type_config=fd.type_config or {},
            default=default_val,
            submodel_config_version_id=sub_id,
            workflow_version_id=fd.workflow_version_id,
            workflow_definition_id=wf_def_id,
        ))

    # Form elements with translations + bindings.
    form_elements_out = []
    for el in version.form_elements.prefetch_related("translations", "bindings__data_field").order_by("sort_order", "id").all():
        label_dict = {t.language: t.label for t in el.translations.all()}
        help_dict = {t.language: t.help_text for t in el.translations.all() if t.help_text}
        bindings_out = [
            FormElementBindingDraftOut(data_field_slug=b.data_field.slug, role=b.role)
            for b in el.bindings.all()
        ]
        form_elements_out.append(FormElementDraftOut(
            slug=el.slug,
            element_type=el.element_type,
            parent_slug=el.parent.slug if el.parent_id else None,
            sort_order=el.sort_order,
            is_preview=el.is_preview,
            labels=label_dict if label_dict else None,
            help_texts=help_dict,
            type_config=el.type_config or {},
            bindings=bindings_out,
        ))

    return ConfigDraftExportOut(
        notes=version.notes,
        data_fields=data_fields_out,
        form_elements=form_elements_out,
    )
