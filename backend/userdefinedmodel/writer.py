"""
Entity write logic: PATCH handler, submodel operations, file promotion.
All writes go through apply_patch() which runs inside transaction.atomic()
with the root UserDefinedModelEntity lock already held.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta, datetime, date, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.timezone import now

if TYPE_CHECKING:
    from userdefinedmodel.models import (
        UserDefinedModelEntityNode,
        UserDefinedModelEntity,
        FieldDefinition,
        FieldValue,
    )
    from openid_user_management.models import OpenIDUser

logger = logging.getLogger(__name__)


def serialize_node(node: "UserDefinedModelEntityNode", viewable: dict[str, list[str]] | None = None) -> dict:
    """Build the EntityOut-compatible dict for a node and its children.

    ``viewable`` is the policy's per-node field grant ``{node_id: [slugs]}``
    (§5): when given, this is the single redaction point — field values and
    child lists are filtered RECURSIVELY for every node in the tree. A node id
    absent from the map exposes nothing. ``None`` disables filtering and is
    reserved for internal (non-API) callers.
    """
    from userdefinedmodel.models import UserDefinedModelEntity

    allowed: set[str] | None = None
    if viewable is not None:
        allowed = set(viewable.get(str(node.id), []))

    field_values = []
    for fv in node.field_values.select_related("field", "value_file").filter(field__version_id=node.config_version_id):
        if allowed is not None and fv.field.slug not in allowed:
            continue
        # For file/image fields use the already-loaded FileAttachment object so
        # _serialize_value can include the URL without an extra query.
        if fv.field.data_type in ("image", "file") and fv.value_file_id is not None:
            val = fv.value_file
        else:
            val = fv.get_value()
        field_values.append({
            "field_slug": fv.field.slug,
            "data_type": fv.field.data_type,
            "value": _serialize_value(val, fv.field),
            "language": fv.language,
        })

    children = {}
    for child in node.children.select_related("parent_field").order_by("submodelinstance__sort_order", "id"):
        slug = child.parent_field.slug if child.parent_field else "unknown"
        if allowed is not None and slug not in allowed:
            continue
        if slug not in children:
            children[slug] = []
        children[slug].append(serialize_node(child, viewable))

    result = {
        "id": str(node.id),
        "config_version_id": str(node.config_version_id),
        "user_defined_model_type_id": None,
        "field_values": field_values,
        "children": children,
        "overflow_data": node.overflow_data,
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
    }

    try:
        entity = node.userdefinedmodelentity
        result["user_defined_model_type_id"] = str(entity.user_defined_model_type_id) if entity.user_defined_model_type_id else None
    except UserDefinedModelEntity.DoesNotExist:
        pass

    return result


def _serialize_value(val, field: "FieldDefinition") -> Any:
    """Serialize a stored value for API output."""
    from userdefinedmodel.models.node import FileAttachment
    if val is None:
        return None
    if isinstance(val, FileAttachment):
        return {
            "id": str(val.id),
            "original_name": val.original_name,
            "mime_type": val.mime_type,
            "url": val.file.url,
        }
    if isinstance(val, uuid.UUID):  # e.g. submodel_select FK target node id
        return str(val)
    # Defensive: if an ORM object slipped through, return its PK as string
    if hasattr(val, "pk") and not isinstance(val, (str, int, float, bool, list, dict)):
        return str(val.pk)
    return val


def apply_patch(
    node: "UserDefinedModelEntityNode",
    changed_fields: dict[str, Any],
    user: "OpenIDUser",
    edit_group=None,
    validate_only: bool = False,
    _old_entity_doc: dict | None = None,
    locale: str | None = None,
    skip_policy: bool = False,
) -> "EditGroup":
    """
    Apply a partial PATCH to node. Must be called inside transaction.atomic()
    with root lock held. Returns the EditGroup created.

    ``validate_only`` suppresses irreversible side effects (staging-file
    promotion) for callers that roll the transaction back.
    ``skip_policy=True`` applies only the writes — no save-policy evaluation,
    no action dispatch, no validate_for_save. Used by the validation-preview
    endpoint, which runs its own single "preview" evaluation afterwards.
    """
    from userdefinedmodel.models import FieldDefinition, FieldValue, UserDefinedModelEntity
    from userdefinedmodel.models.history import EditGroup, FieldEdit
    from userdefinedmodel.models.node import StagingFile, FileAttachment

    # Build field map for this version
    field_map = {
        f.slug: f
        for f in node.config_version.field_definitions.all()
    }

    # Split into scalar vs submodel_list entries
    scalar_changes = {}
    submodel_changes = {}
    unknown_slugs = []
    for slug, value in changed_fields.items():
        # Keys prefixed with "_" are reserved UI control markers (e.g. "_undelete"
        # sent by the submodel "Restore" button), never real field slugs.
        if slug.startswith("_"):
            continue
        field = field_map.get(slug)
        if field is None:
            unknown_slugs.append(slug)
            continue
        if field.data_type in FieldDefinition.STRUCTURAL_TYPES:
            # Layout fields have no value; silently skip them
            continue
        if field.data_type == FieldDefinition.DataType.SUBMODEL_LIST:
            submodel_changes[slug] = value
        elif field.data_type == FieldDefinition.DataType.WORKFLOW:
            # Workflow state is read-only via PATCH; advance via /transition/ endpoint
            raise ValidationError({slug: ["Workflow state cannot be set directly. Use the /transition/ endpoint."]})
        else:
            scalar_changes[slug] = (field, value)

    # Reject unknown slugs instead of silently dropping them: a PATCH against a
    # config version that lacks the field (e.g. an entity pinned to an archived
    # version) must fail loudly rather than appear to save and write nothing.
    if unknown_slugs:
        raise ValidationError({
            slug: ["Unknown field for this entity's config version."]
            for slug in unknown_slugs
        })

    # Build or reuse edit group
    try:
        root_entity = node.userdefinedmodelentity
    except UserDefinedModelEntity.DoesNotExist:
        root_entity = node.get_root()

    # Capture the pre-write root entity document once at the top level so the
    # policy can inspect state that no longer exists after writes (e.g. the author
    # field of a deleted review submodel). Raw PKs only — the engine resolves
    # references via the input.users/groups/linked_entities lookup maps.
    if _old_entity_doc is None:
        _old_entity_doc = root_entity.to_policy_document()

    if edit_group is None:
        edit_group = EditGroup.objects.create(node=node, root_entity=root_entity, saved_by=user)

    # 8. Apply scalar writes first (so validate_for_save sees the new values)
    for slug, (field, value) in scalar_changes.items():
        _apply_scalar_write(node, field, value, user, edit_group)

    # 9. Process submodel_list operations
    for slug, ops in submodel_changes.items():
        field = field_map[slug]
        if isinstance(ops, list):
            _apply_submodel_ops(node, field, ops, user, edit_group, validate_only=validate_only, old_entity_doc=_old_entity_doc)

    if skip_policy:
        # Nested submodel patches and the validation-preview path: the ONE
        # root-level evaluation covers the whole tree (contract §3.3-12), so no
        # per-node policy runs here. Save rules still apply per node unless the
        # caller validates the subtree itself.
        if not validate_only:
            node.validate_for_save()
        return edit_group, []

    # Evaluate policy for SAVE before validation so PRE actions can normalise
    # field values that validation will then check.
    output, messages = _evaluate_save_policy(node, user, changed_fields, old_entity_doc=_old_entity_doc, locale=locale)

    # Dispatch PRE-phase save actions (after writes, before validation).
    from userdefinedmodel.actions import ActionContext, dispatch_actions
    pre_ctx = ActionContext(
        node=node,
        user=user,
        trigger="save",
        phase="pre",
        edit_group=edit_group,
    )
    dispatch_actions(output.actions, pre_ctx)

    # 7. Validate for save (runs on the new state; transaction rolls back on failure)
    node.validate_for_save()

    # Dispatch POST-phase save actions (after validation).
    post_ctx = pre_ctx.model_copy(update={"phase": "post"})
    dispatch_actions(output.actions, post_ctx)

    return edit_group, messages


def _evaluate_save_policy(node, user, changed_fields: dict, old_entity_doc: dict | None = None, locale: str | None = None):
    """Evaluate Rego policy for SAVE action.

    Returns ``(PolicyEvaluationOutput, messages_list)``.
    Raises PolicyError if allow=False.
    """
    from userdefinedmodel.engine import (
        evaluate_policy, evaluate_view_precheck, build_entity_document, PolicyError,
    )

    safe_changed = serialize_changed_fields(changed_fields)

    logger.debug(
        "policy save eval node=%s user=%s changed_slugs=%s",
        node.id, user.username, list(safe_changed.keys()),
    )

    if old_entity_doc is None:
        old_entity_doc = build_entity_document(node)
    _view_allowed, additional_result = evaluate_view_precheck(node, user, old_entity_doc, locale=locale)

    output = evaluate_policy(
        node, user, "save",
        locale=locale,
        changed_fields=safe_changed,
        old_entity_doc=old_entity_doc,
        additional_result=additional_result,
    )

    logger.debug(
        "policy save result node=%s allow=%s messages=%s",
        node.id, output.allow, output.messages,
    )

    messages = output.messages

    if not output.allow:
        raise PolicyError(messages or [{"level": "critical", "text": "Save denied by policy."}])

    return output, messages


def serialize_changed_fields(changed_fields: dict) -> dict:
    """Wrap the raw submitted payload as {slug: {"value": <json-safe>}} for the
    policy input (same scalar encoding as entity field values)."""
    import decimal
    import datetime as dt

    def _safe(v):
        if isinstance(v, decimal.Decimal):
            return float(v)
        if isinstance(v, (dt.datetime, dt.date, dt.time)):
            return v.isoformat()
        if hasattr(v, "pk"):
            return str(v.pk)
        return v

    return {slug: {"value": _safe(val)} for slug, val in changed_fields.items()}


def _apply_scalar_write(node, field, value, user, edit_group) -> None:
    from userdefinedmodel.models.node import FieldValue, StagingFile, FileAttachment
    from userdefinedmodel.models.history import FieldEdit

    lang = ""  # non-localized by default

    if field.is_localized and isinstance(value, dict):
        # Localized: value is {lang_code: val} dict
        for lang_code, lang_val in value.items():
            _write_field_value(node, field, lang_val, lang_code, user, edit_group)
        return
    elif field.is_localized and value is None:
        # Clear all language values
        for fv in node.field_values.filter(field=field):
            _record_field_edit(edit_group, field, fv.get_value(), None, lang=fv.language, affected_node=node)
            fv.delete()
        return

    _write_field_value(node, field, value, lang, user, edit_group)


def _write_field_value(node, field, value, language, user, edit_group) -> None:
    from userdefinedmodel.models.node import FieldValue, StagingFile, FileAttachment
    from userdefinedmodel.models.history import FieldEdit

    fv = node.field_values.filter(field=field, language=language).first()
    old_value = fv.get_value() if fv else None
    old_attachment = fv.value_file if fv and hasattr(fv, "value_file") else None

    # submodel_select: {"op": "create"} or {"op": "delete"}
    if field.data_type == "submodel_select" and isinstance(value, dict):
        op = value.get("op")
        if op == "create":
            from userdefinedmodel.models.node import SubmodelInstance
            if not field.submodel_config_id:
                raise ValidationError({field.slug: "No submodel_config set on this field."})
            child = SubmodelInstance.objects.create(
                config_version_id=field.submodel_config_id,
                parent_node=node,
                parent_field=field,
                sort_order=0,
            )
            child.materialize_defaults()
            child.materialize_user_defaults(user)
            # Apply caller-supplied field values before setting the initial state
            op_fields = value.get("fields") or {}
            if op_fields:
                _, _msgs = apply_patch(child, op_fields, user, edit_group, skip_policy=True)
            value = child.id  # fall through to set value_node_id
        elif op == "update":
            # Update the fields of the currently-referenced child; the FK itself
            # does not change, so write nothing to value_node and return early.
            if fv and fv.value_node_id:
                from userdefinedmodel.models.node import SubmodelInstance
                try:
                    child = SubmodelInstance.objects.get(id=fv.value_node_id, parent_node=node)
                except SubmodelInstance.DoesNotExist:
                    child = None
                op_fields = value.get("fields") or {}
                if child and op_fields:
                    _, _msgs = apply_patch(child, op_fields, user, edit_group, skip_policy=True)
            return
        elif op == "delete":
            if fv and fv.value_node_id:
                from userdefinedmodel.models.node import SubmodelInstance
                try:
                    SubmodelInstance.objects.get(id=fv.value_node_id, parent_node=node).delete()
                except SubmodelInstance.DoesNotExist:
                    pass
            value = None  # clear the FK

    # Defensive: single-value FK fields must never receive a list (that is the
    # submodel_list ops shape). Fail with a clear message instead of a cryptic
    # "not a valid UUID" deep in model validation.
    if field.data_type in ("submodel_select", "entity_select", "user_select", "group_select") and isinstance(value, list):
        raise ValidationError({field.slug: f"{field.data_type} expects a single value, not a list."})

    # Handle file staging promotion
    if isinstance(value, dict) and "staging_id" in value:
        staging_id = value["staging_id"]
        try:
            staging = StagingFile.objects.get(id=staging_id, uploader=user)
        except StagingFile.DoesNotExist:
            raise ValidationError({field.slug: "Staging file not found or not owned by you."})

        # Create FileAttachment from staging
        attachment = FileAttachment.objects.create(
            original_name=staging.original_name,
            mime_type=staging.mime_type,
            size_bytes=staging.size_bytes,
            file=staging.file,
        )

        # Soft-delete old attachment if nothing else references it
        if old_attachment:
            other_refs = FieldValue.objects.filter(value_file=old_attachment).exclude(pk=fv.pk if fv else None).count()
            if other_refs == 0:
                from django.utils.timezone import now
                old_attachment.deleted_at = now()
                old_attachment.save(update_fields=["deleted_at"])

        if fv is None:
            fv = FieldValue(node=node, field=field, language=language)
        fv.value_file = attachment
        fv.save()
        staging.delete()

        _record_field_edit(edit_group, field, None, None, old_attachment=old_attachment, new_attachment=attachment, lang=language, affected_node=node)
        return

    # Null = clear
    if value is None:
        if fv:
            if old_attachment:
                other_refs = FieldValue.objects.filter(value_file=old_attachment).exclude(pk=fv.pk).count()
                if other_refs == 0:
                    from django.utils.timezone import now
                    old_attachment.deleted_at = now()
                    old_attachment.save(update_fields=["deleted_at"])
            _record_field_edit(edit_group, field, old_value, None, old_attachment=old_attachment, lang=language, affected_node=node)
            fv.delete()
        return

    # Normal scalar write
    if fv is None:
        fv = FieldValue(node=node, field=field, language=language)
    fv.set_value(value, field=field)
    fv.full_clean()
    fv.save()
    _record_field_edit(edit_group, field, old_value, value, lang=language, affected_node=node)


def _record_field_edit(edit_group, field, old_value, new_value, *, old_attachment=None, new_attachment=None, lang="", affected_node=None) -> None:
    from userdefinedmodel.models.history import FieldEdit

    # Serialize old/new values to JSON-compatible
    def _json(v):
        if v is None:
            return None
        if hasattr(v, "pk"):
            return str(v.pk)
        # Bare UUIDs (e.g. a submodel_select FK target) and other non-JSON
        # scalars must be stringified before hitting the JSONField.
        if isinstance(v, uuid.UUID):
            return str(v)
        if isinstance(v, (datetime, date, time, Decimal)):
            return str(v)
        return v

    FieldEdit.objects.create(
        group=edit_group,
        change_kind=FieldEdit.ChangeKind.FIELD_VALUE,
        field=field,
        language=lang,
        old_value=_json(old_value),
        new_value=_json(new_value),
        old_attachment=old_attachment,
        new_attachment=new_attachment,
        affected_node=affected_node,
    )


def _apply_submodel_ops(parent_node, field, ops, user, edit_group, validate_only: bool = False, old_entity_doc: dict | None = None) -> None:
    from userdefinedmodel.models.node import SubmodelInstance
    from userdefinedmodel.models.history import FieldEdit

    for op_data in ops:
        op = op_data.get("op")
        op_id = op_data.get("id")
        op_fields = op_data.get("fields", {})
        sort_order = op_data.get("sort_order")

        if op == "create":
            if field.submodel_config is None:
                from userdefinedmodel.engine import PolicyError
                raise PolicyError([{
                    "level": "error",
                    "text": (
                        f"Submodel field '{field.slug}' has no config version assigned. "
                        "Link a published config version to this field before adding items."
                    ),
                }])

            # Determine sort_order
            if sort_order is None:
                max_order = parent_node.children.filter(parent_field=field).aggregate(
                    m=__import__("django.db.models", fromlist=["Max"]).Max("submodelinstance__sort_order")
                )["m"] or 0
                sort_order = max_order + 1

            child = SubmodelInstance.objects.create(
                config_version=field.submodel_config,
                parent_node=parent_node,
                parent_field=field,
                sort_order=sort_order,
            )

            child.materialize_defaults()
            child.materialize_user_defaults(user)

            if op_fields:
                _, _msgs = apply_patch(child, op_fields, user, edit_group=edit_group, validate_only=validate_only, _old_entity_doc=old_entity_doc, skip_policy=True)

            FieldEdit.objects.create(
                group=edit_group,
                change_kind=FieldEdit.ChangeKind.NODE_ADDED,
                field=field,
                affected_node=child,
            )

        elif op == "update":
            try:
                child = SubmodelInstance.objects.get(id=op_id, parent_node=parent_node)
            except SubmodelInstance.DoesNotExist:
                raise ValidationError({field.slug: f"Submodel instance {op_id} not found."})

            if sort_order is not None and child.sort_order != sort_order:
                old_order = child.sort_order
                child.sort_order = sort_order
                child.save(update_fields=["sort_order"])
                FieldEdit.objects.create(
                    group=edit_group,
                    change_kind=FieldEdit.ChangeKind.NODE_REORDERED,
                    field=field,
                    affected_node=child,
                    old_value={"sort_order": old_order},
                    new_value={"sort_order": sort_order},
                )

            if op_fields:
                _, _msgs = apply_patch(child, op_fields, user, edit_group=edit_group, validate_only=validate_only, _old_entity_doc=old_entity_doc, skip_policy=True)

        elif op == "delete":
            try:
                child = SubmodelInstance.objects.get(id=op_id, parent_node=parent_node)
            except SubmodelInstance.DoesNotExist:
                raise ValidationError({field.slug: f"Submodel instance {op_id} not found."})

            FieldEdit.objects.create(
                group=edit_group,
                change_kind=FieldEdit.ChangeKind.NODE_REMOVED,
                field=field,
                affected_node=child,
            )
            child.delete()
