"""
Workflow transition engine (§15) and policy evaluation (§16).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

if TYPE_CHECKING:
    from userdefinedmodel.models import (
        UserDefinedModelEntityNode,
        UserDefinedModelEntity,
        WorkflowTransition,
    )
    from openid_user_management.models import OpenIDUser

logger = logging.getLogger(__name__)

# regorus renders an undefined rule result as this JSON string. It must never be
# treated as a truthy value (bool("<undefined>") is True), or deny-by-default fails open.
_UNDEFINED = "<undefined>"


# ─── User / group serialisation helpers ──────────────────────────────────────

def _serialize_user(user, *, include_group_members: bool = True) -> dict:
    """Return a rich user dict for the Rego input document.

    When include_group_members=True each group entry also contains a 'members'
    list (users without their own recursive group members, to cap depth at 2).
    """
    groups = []
    for g in user.groups.all():
        entry: dict = {"id": g.id, "name": g.name}
        if include_group_members:
            entry["members"] = [
                _serialize_user(m, include_group_members=False)
                for m in g.user_set.prefetch_related("groups").all()
            ]
        groups.append(entry)
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "phone_number": user.phone_number,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "groups": groups,
    }


def _serialize_group(group) -> dict:
    """Return a rich group dict for the Rego input document, including members."""
    return {
        "id": group.id,
        "name": group.name,
        "members": [
            _serialize_user(m, include_group_members=True)
            for m in group.user_set.prefetch_related("groups").all()
        ],
    }


def _expand_fields(fields_data: dict) -> None:
    """Expand user_select / group_select values from raw PKs to rich objects, in-place."""
    from openid_user_management.models import OpenIDUser
    from django.contrib.auth.models import Group

    # Collect IDs first for batch loading
    user_ids = []
    group_ids = []
    for entry in fields_data.values():
        dt = entry.get("data_type")
        val = entry.get("value")
        if val is None:
            continue
        if dt == "user_select":
            user_ids.append(val)
        elif dt == "user_select_multi" and isinstance(val, list):
            user_ids.extend(val)
        elif dt == "group_select":
            group_ids.append(int(val))
        elif dt == "group_select_multi" and isinstance(val, list):
            group_ids.extend(int(v) for v in val)

    users = {}
    if user_ids:
        for u in OpenIDUser.objects.prefetch_related("groups__user_set__groups").filter(id__in=user_ids):
            users[str(u.id)] = u

    groups = {}
    if group_ids:
        for g in Group.objects.prefetch_related("user_set__groups").filter(id__in=group_ids):
            groups[g.id] = g

    for entry in fields_data.values():
        dt = entry.get("data_type")
        val = entry.get("value")
        if val is None:
            continue
        if dt == "user_select":
            u = users.get(str(val))
            if u:
                entry["value"] = _serialize_user(u)
        elif dt == "user_select_multi" and isinstance(val, list):
            entry["value"] = [_serialize_user(users[str(v)]) for v in val if str(v) in users]
        elif dt == "group_select":
            g = groups.get(int(val))
            if g:
                entry["value"] = _serialize_group(g)
        elif dt == "group_select_multi" and isinstance(val, list):
            entry["value"] = [_serialize_group(groups[int(v)]) for v in val if int(v) in groups]


# ─── Policy evaluation (§16) ──────────────────────────────────────────────────

def _normalize_policy_messages(messages: list) -> list:
    """Convert Rego field_slug shorthand to highlight_fields list expected by the frontend."""
    result = []
    for m in messages:
        if not isinstance(m, dict) or 'field_slug' not in m:
            result.append(m)
            continue
        m = dict(m)
        field_slug = m.pop('field_slug')
        m['highlight_fields'] = [field_slug] if field_slug is not None else []
        result.append(m)
    return result


def build_policy_input(node: "UserDefinedModelEntityNode", user: "OpenIDUser", action: str, **kwargs) -> dict:
    """Build the input document passed to regorus for a given action.

    For SAVE action, pass changed_fields=<incoming_dict> so Rego can see both
    the new entity state (input.entity, post-write) and which fields changed
    (input.changed_fields, each wrapped as {"value": ...}).

    When node is a submodel the policy document is built from the root entity so
    the policy always has access to the full proposal context (owner, status,
    reviewer lists, etc.) regardless of which node triggered the evaluation.
    """
    root = node.get_root()
    context_node = root if root.pk != node.pk else node
    policy_doc = context_node.to_policy_document()
    logger.debug("build_policy_input node=%s action=%s user=%s", node.id, action, user.username)

    user_doc = _serialize_user(user, include_group_members=True)
    user_doc["permissions"] = list(user.user_permissions.values_list("codename", flat=True))

    # Expand user_select / group_select field values to rich objects
    _expand_fields(policy_doc.get("fields", {}))
    for child_list in policy_doc.get("children", {}).values():
        for child_doc in child_list:
            _expand_fields(child_doc.get("fields", {}))

    input_doc = {
        "action": action,
        "entity": policy_doc,      # current (old) values + unchanged fields
        "user": user_doc,
        "type_id": policy_doc.get("type_id"),
        "changed_fields": None,    # overridden for SAVE action
        "transition": None,        # overridden for TRANSITION action
        "field": None,
        **kwargs,
    }
    return input_doc


def get_udm_type_for_node(node: "UserDefinedModelEntityNode"):
    """Return the UserDefinedModelType for a node (root or submodel)."""
    try:
        return node.userdefinedmodelentity.user_defined_model_type
    except Exception:
        root = node.get_root()
        return root.user_defined_model_type if root else None


def evaluate_policy(node: "UserDefinedModelEntityNode", user: "OpenIDUser", action: str, **kwargs) -> "PolicyEvaluationOutput":
    """Evaluate all policies for node's UDMType; return a validated PolicyEvaluationOutput.

    Default-deny: if the node has no UDMType, or its type has no policies attached,
    nothing is permitted and no fields are exposed. Access must be granted by an
    explicit policy clause.
    """
    from userdefinedmodel.actions import PolicyEvaluationOutput

    _deny = PolicyEvaluationOutput(allow=False, messages=[], viewable_fields=[], editable_fields=[])

    udm_type = get_udm_type_for_node(node)
    if udm_type is None:
        return _deny

    from userdefinedmodel.models import UserDefinedModelTypePolicy
    type_policies = list(
        udm_type.type_policies.select_related("policy").order_by("sort_order")
    )
    if not type_policies:
        return _deny

    try:
        import regorus
        eng = regorus.Engine()
        for tp in type_policies:
            logger.debug("loading policy slug=%s for node=%s", tp.policy.slug, node.id)
            eng.add_policy(f"policy_{tp.policy.slug}.rego", tp.policy.source)

        input_doc = build_policy_input(node, user, action, **kwargs)
        logger.debug("policy input node=%s action=%s: %s", node.id, action, json.dumps(input_doc))
        eng.set_input_json(json.dumps(input_doc))
        eng.set_gather_prints(True)

        def _eval_list(rule_path: str, default=None):
            """Evaluate a Rego rule that should return a list; return default on undefined."""
            try:
                raw = json.loads(eng.eval_rule_as_json(rule_path))
                if isinstance(raw, list):
                    return raw
                return default  # includes the "<undefined>" sentinel
            except Exception:
                return default

        def _eval_bool(rule_path: str, default: bool = True) -> bool:
            """Evaluate a Rego rule that should return a bool; return default on undefined."""
            try:
                raw = json.loads(eng.eval_rule_as_json(rule_path))
                # regorus serializes an undefined rule (no matching clause) as the
                # JSON string "<undefined>". Treat it as the default — NOT as a
                # truthy non-empty string — so deny-by-default actually denies.
                if raw is None or raw == _UNDEFINED:
                    return default
                if isinstance(raw, list):
                    return bool(raw[0]) if raw else default
                return bool(raw)
            except Exception:
                return default

        # Deny by default: undefined allow rule = false (secure by default)
        allow = _eval_bool("data.udm.allow", default=False)
        raw_messages = _eval_list("data.udm.messages", default=[])
        messages = _normalize_policy_messages(raw_messages)
        viewable_fields = _eval_list("data.udm.viewable_fields", default=None)
        editable_fields = _eval_list("data.udm.editable_fields", default=[])
        dashboard_columns_raw = _eval_list("data.udm.dashboard_columns", default=[])
        dashboard_columns = [c for c in (dashboard_columns_raw or []) if isinstance(c, dict)]
        raw_actions = _eval_list("data.udm.actions", default=[])

        if logger.isEnabledFor(logging.DEBUG):
            try:
                full_doc = eng.eval_query_as_json("data.udm")
                debug_data = json.loads(full_doc)
                logger.debug("policy full document node=%s action=%s: %s", node.id, action, debug_data)
            except Exception as exc:
                logger.debug("policy full document unavailable: %s", exc)

        prints = eng.take_prints()
        if prints and logger.isEnabledFor(logging.DEBUG):
            logger.debug("policy prints node=%s action=%s:\n%s", node.id, action, prints)

        logger.debug(
            "policy result node=%s action=%s allow=%s messages=%d viewable=%s editable=%s actions=%d",
            node.id, action, allow, len(messages),
            viewable_fields, editable_fields, len(raw_actions or []),
        )
        return PolicyEvaluationOutput(
            allow=allow,
            messages=messages,
            viewable_fields=viewable_fields,
            editable_fields=editable_fields or [],
            dashboard_columns=dashboard_columns,
            actions=raw_actions or [],
        )
    except Exception as exc:
        logger.exception("Policy evaluation failed: %s", exc)
        return _deny


def evaluate_type_public_fields(udm_type, user=None) -> tuple[dict, dict]:
    """Evaluate data.udm.public_type_fields and data.udm.TYPE_DESCRIPTION.

    Runs all policies for the type with a minimal input that includes the
    requesting user so that rules can vary per-user.

    Returns (fields_dict, descriptions) where descriptions is a
    ``{lang_code: markdown}`` dict.  A plain-string TYPE_DESCRIPTION is
    normalised to ``{"": value}``.  Both default to empty when no policies are
    attached, the rule is undefined, or evaluation fails.
    """
    type_policies = list(
        udm_type.type_policies.select_related("policy").order_by("sort_order")
    )
    logger.debug("evaluate_type_public_fields type=%s policies=%d", udm_type.id, len(type_policies))
    if not type_policies:
        return {}, {}
    try:
        import regorus
        eng = regorus.Engine()
        for tp in type_policies:
            logger.debug("evaluate_type_public_fields loading policy slug=%s", tp.policy.slug)
            eng.add_policy(f"policy_{tp.policy.slug}.rego", tp.policy.source)
        input_doc: dict = {"action": "public_type_fields"}
        if user is not None:
            input_doc["user"] = _serialize_user(user)
        logger.debug("evaluate_type_public_fields input=%s", json.dumps(input_doc))
        eng.set_input_json(json.dumps(input_doc))

        def _query_value(query: str):
            result = eng.eval_query(query)
            rows = result.get("result", result) if isinstance(result, dict) else result
            if rows and isinstance(rows, list):
                return rows[0].get("expressions", [{}])[0].get("value")
            return None

        fields_val = _query_value("data.udm.public_type_fields")
        fields = fields_val if isinstance(fields_val, dict) else {}

        desc_val = _query_value("data.udm.TYPE_DESCRIPTION")
        if isinstance(desc_val, str):
            descriptions = {"": desc_val}
        elif isinstance(desc_val, dict):
            descriptions = {str(k): str(v) for k, v in desc_val.items() if isinstance(v, str)}
        else:
            descriptions = {}

        logger.debug("evaluate_type_public_fields fields=%r descriptions keys=%r", fields, list(descriptions))
        return fields, descriptions
    except Exception as exc:
        logger.debug("evaluate_type_public_fields failed: %s", exc, exc_info=True)
        return {}, {}


# ─── Policy / transition exceptions ──────────────────────────────────────────

class PolicyError(Exception):
    """Raised when policy blocks a save. Carries the full messages list so the
    API can return structured highlight information to the frontend."""
    def __init__(self, messages: list):
        super().__init__("Save blocked by policy")
        self.messages = messages


class TransitionError(Exception):
    """Raised when a workflow transition cannot proceed."""
    def __init__(self, message: str, http_status: int = 422, details=None):
        super().__init__(message)
        self.http_status = http_status
        self.details = details or {}


def execute_transition(
    node: "UserDefinedModelEntityNode",
    field_slug: str,
    name: str,
    user: "OpenIDUser",
    edit_group=None,
    _visited: frozenset = frozenset(),
    _depth: int = 0,
    _system: bool = False,
) -> list:
    """
    Execute a named workflow transition on the specified workflow field of `node`.
    Must be called inside an existing transaction.atomic() with the root lock held.

    ``_visited`` and ``_depth`` are internal cycle-guard parameters threaded by
    :class:`~userdefinedmodel.actions.TriggerTransitionOutput` handlers; callers
    should not set them.

    ``_system=True`` bypasses the policy authorization check.  Use this only when
    the call originates from a policy action that was itself already authorized —
    e.g. a ``trigger_transition`` action cascading to child nodes.
    """
    from userdefinedmodel.actions import ActionContext, dispatch_actions
    from userdefinedmodel.models import WorkflowTransition, FieldDefinition, FieldValue
    from userdefinedmodel.models.history import EditGroup, FieldEdit

    # Locate the workflow field definition on this node's config version
    try:
        field_def = node.config_version.field_definitions.select_related("workflow_version").get(
            slug=field_slug, data_type=FieldDefinition.DataType.WORKFLOW
        )
    except FieldDefinition.DoesNotExist:
        raise TransitionError(f"No workflow field '{field_slug}' on this node.", http_status=404)

    if not field_def.workflow_version_id:
        raise TransitionError(f"Workflow field '{field_slug}' has no workflow version.", http_status=404)

    # Get the current state from the field value
    fv = FieldValue.objects.filter(node=node, field=field_def, language="").select_related("value_workflow_state").first()
    current_state = fv.value_workflow_state if fv else None

    try:
        transition = WorkflowTransition.objects.get(
            version=field_def.workflow_version, name=name
        )
    except WorkflowTransition.DoesNotExist:
        raise TransitionError(f"Transition '{name}' not found in workflow '{field_slug}'.", http_status=404)

    # Check from_state / from_undefined_only
    if transition.from_undefined_only:
        if current_state is not None:
            raise TransitionError(
                f"Transition '{name}' only allowed from undefined state, but field '{field_slug}' is in '{current_state.name}'.",
                http_status=409,
            )
    elif transition.from_state is not None:
        if current_state is None or current_state.id != transition.from_state_id:
            current_name = current_state.name if current_state else "None"
            raise TransitionError(
                f"Field '{field_slug}' is in state '{current_name}', but transition '{name}' requires '{transition.from_state.name}'.",
                http_status=409,
            )

    # Evaluate policy — pass field slug and node id so Rego can see which workflow
    # is transitioning and (for child nodes) which specific node is affected.
    # _system=True skips this check: the call originates from an already-authorized
    # policy action, so re-evaluating would incorrectly apply user-level rules.
    if _system:
        from userdefinedmodel.actions import PolicyEvaluationOutput
        output = PolicyEvaluationOutput(allow=True)
    else:
        output = evaluate_policy(node, user, "transition", transition=name, field=field_slug, node_id=str(node.id))
        if not output.allow:
            msgs = output.messages
            if msgs:
                raise TransitionError(
                    f"Policy denied transition '{name}'.",
                    http_status=422,
                    details={"policy_messages": msgs},
                )
            raise TransitionError(f"Policy denied transition '{name}'.", http_status=403)

    # Build shared context for pre/post dispatch
    if edit_group is None:
        root_entity = None
        try:
            root_entity = node.userdefinedmodelentity
        except Exception:
            root_entity = node.get_root()
        edit_group = EditGroup.objects.create(
            node=node,
            root_entity=root_entity,
            saved_by=user,
        )

    pre_ctx = ActionContext(
        node=node,
        user=user,
        trigger="transition",
        phase="pre",
        edit_group=edit_group,
        visited_transitions=_visited,
        depth=_depth,
    )
    dispatch_actions(output.actions, pre_ctx)

    # Subtree validation (save-rule floor)
    _validate_subtree(node)

    # Apply state change to the workflow field value
    old_state_name = current_state.name if current_state else None
    if fv is None:
        fv = FieldValue.objects.create(node=node, field=field_def, language="")
    fv.value_workflow_state = transition.to_state
    fv.save(update_fields=["value_workflow_state"])

    # Record transition in history
    FieldEdit.objects.create(
        group=edit_group,
        change_kind=FieldEdit.ChangeKind.NODE_TRANSITION,
        field=field_def,
        affected_node=node,
        old_value={"state": old_state_name},
        new_value={"state": transition.to_state.name},
    )

    post_ctx = pre_ctx.model_copy(update={"phase": "post"})
    dispatch_actions(output.actions, post_ctx)

    return output.messages


def _validate_subtree(node: "UserDefinedModelEntityNode") -> None:
    """Re-run save rules on every node in the subtree (§4)."""
    from django.core.exceptions import ValidationError
    try:
        node.validate_for_save()
    except ValidationError as exc:
        errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": exc.messages}
        raise TransitionError(
            "Subtree validation failed",
            http_status=422,
            details={"field_errors": errors},
        )
    for child in node.children.all():
        _validate_subtree(child)
