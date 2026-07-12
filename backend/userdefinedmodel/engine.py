"""
Workflow transition engine (§15) and policy evaluation (§16).

Input/output contract: documentation/configuration/policies/_input_schema.rego
(mirrored by userdefinedmodel.policy_input). One evaluation per request, always
on the ROOT entity document; the engine reads exactly one aggregate rule:
``data.udm.result`` for entity actions, ``data.udm.type_result`` for
``public_type_fields``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import TYPE_CHECKING, ClassVar, Optional

from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError

from userdefinedmodel.policy_input import INPUT_VERSION, validate_policy_input

if TYPE_CHECKING:
    from userdefinedmodel.models import (
        UserDefinedModelEntityNode,
        WorkflowTransition,
    )
    from openid_user_management.models import OpenIDUser

logger = logging.getLogger(__name__)

# regorus renders an undefined rule result as this JSON string. It must never be
# treated as a truthy value (bool("<undefined>") is True), or deny-by-default fails open.
_UNDEFINED = "<undefined>"

# entity_select targets are resolved into input.linked_entities exactly this
# many entities deep (contract §3.2-8). Raise deliberately if policies need it.
LINKED_ENTITY_DEPTH = 1


# ─── Rego session + per-type compiled engine cache ───────────────────────────

class RegoSession:
    """A compiled regorus engine over a fixed set of policy sources.

    Compile once, ``clone()`` per evaluation — regorus engines are cheap to
    clone but expensive to compile. Shared by the real evaluation path and the
    introspection endpoint so the two can never drift.
    """

    def __init__(self, sources: list[tuple[str, str]]):
        import regorus

        self._engine = regorus.Engine()
        for filename, source in sources:
            self._engine.add_policy(filename, source)
        self.sources = sources

    def clone(self):
        return self._engine.clone()

    @staticmethod
    def eval_rule(engine, rule_path: str):
        """Evaluate a rule; return the parsed JSON value or None when undefined."""
        raw = json.loads(engine.eval_rule_as_json(rule_path))
        if raw == _UNDEFINED:
            return None
        return raw

    def evaluate(self, input_doc: dict, rule_path: str, *, gather_prints: bool = False):
        """One evaluation on a fresh clone. Returns (value, prints)."""
        eng = self.clone()
        eng.set_input_json(json.dumps(input_doc))
        if gather_prints:
            eng.set_gather_prints(True)
        value = self.eval_rule(eng, rule_path)
        prints = eng.take_prints() if gather_prints else []
        return value, prints


# PyO3 regorus engines are UNSENDABLE: an Engine (and its clones) may only be
# used on the thread that created it. The cache is therefore thread-local —
# still bounded to one entry per udm_type_id, per serving thread.
_ENGINE_CACHE_TLS = threading.local()


def _engine_cache() -> dict[str, tuple[str, "RegoSession"]]:
    cache = getattr(_ENGINE_CACHE_TLS, "cache", None)
    if cache is None:
        cache = {}
        _ENGINE_CACHE_TLS.cache = cache
    return cache


def _policy_sources_for_type(udm_type) -> list[tuple[str, str]]:
    return [
        (f"policy_{tp.policy.slug}.rego", tp.policy.source)
        for tp in udm_type.type_policies.select_related("policy").order_by("sort_order")
    ]


def _sources_hash(sources: list[tuple[str, str]]) -> str:
    h = hashlib.sha256()
    for filename, source in sources:
        h.update(filename.encode())
        h.update(b"\0")
        h.update(source.encode())
        h.update(b"\0")
    return h.hexdigest()


def get_session_for_type(udm_type) -> Optional[RegoSession]:
    """Return the cached compiled session for a type, or None when the type has
    no policies attached (default deny). ONE entry per udm_type_id: a changed
    policy hash replaces the entry, so the cache is bounded by the number of
    types and stale versions never pile up."""
    sources = _policy_sources_for_type(udm_type)
    if not sources:
        return None
    key = str(udm_type.id)
    digest = _sources_hash(sources)
    cache = _engine_cache()
    cached = cache.get(key)
    if cached and cached[0] == digest:
        return cached[1]
    session = RegoSession(sources)
    cache[key] = (digest, session)
    return session


def clear_engine_cache() -> None:
    """Clear THIS thread's cache. Other threads self-heal via the source hash."""
    _engine_cache().clear()


# ─── User / group serialisation ───────────────────────────────────────────────

def _serialize_user(user, *, include_permissions: bool = False) -> dict:
    """UserDocument for the input document. Group membership is NOT embedded;
    resolve members via input.groups[gid].member_ids (contract §3.2-7)."""
    doc = {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "phone_number": user.phone_number,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "groups": [{"id": g.id, "name": g.name} for g in user.groups.all()],
    }
    if include_permissions:
        doc["permissions"] = list(user.user_permissions.values_list("codename", flat=True))
    return doc


def _serialize_group(group) -> dict:
    """GroupDocument: members as a flat id list; user data lives in input.users."""
    return {
        "id": group.id,
        "name": group.name,
        "member_ids": [str(u.id) for u in group.user_set.all()],
    }


# ─── Input document assembly ──────────────────────────────────────────────────

def _walk_doc_nodes(node_doc: dict):
    yield node_doc
    for siblings in (node_doc.get("children") or {}).values():
        for child in siblings:
            yield from _walk_doc_nodes(child)


def _collect_reference_ids(node_docs: list[dict]) -> tuple[set, set, set]:
    """Scan field values of all nodes for user / group / entity PKs."""
    user_ids: set[str] = set()
    group_ids: set[int] = set()
    entity_ids: set[str] = set()
    for doc in node_docs:
        for node in _walk_doc_nodes(doc):
            for entry in node.get("fields", {}).values():
                dt = entry.get("data_type")
                val = entry.get("value")
                if val is None:
                    continue
                if dt == "user_select":
                    user_ids.add(str(val))
                elif dt == "user_select_multi" and isinstance(val, list):
                    user_ids.update(str(v) for v in val)
                elif dt == "group_select":
                    group_ids.add(int(val))
                elif dt == "group_select_multi" and isinstance(val, list):
                    group_ids.update(int(v) for v in val)
                elif dt in ("entity_select",):
                    entity_ids.add(str(val))
                elif dt == "entity_select_multi" and isinstance(val, list):
                    entity_ids.update(str(v) for v in val)
    return user_ids, group_ids, entity_ids


def build_lookup_maps(entity_docs: list[dict], schemas: dict) -> tuple[dict, dict, dict]:
    """Build input.users / input.groups / input.linked_entities for the given
    node documents. Linked entities are resolved LINKED_ENTITY_DEPTH deep; their
    user/group references are included in the lookup maps, their own entity
    links stay raw PKs."""
    from django.contrib.auth.models import Group
    from openid_user_management.models import OpenIDUser
    from userdefinedmodel.models import UserDefinedModelEntityNode

    user_ids, group_ids, entity_ids = _collect_reference_ids(entity_docs)

    linked_entities: dict[str, dict] = {}
    seen_entity_ids = {str(n.get("id")) for d in entity_docs for n in _walk_doc_nodes(d)}
    frontier = entity_ids - seen_entity_ids
    for _ in range(LINKED_ENTITY_DEPTH):
        if not frontier:
            break
        nodes = UserDefinedModelEntityNode.objects.filter(id__in=frontier).select_related("config_version__config")
        next_docs = []
        for n in nodes:
            doc = n.to_policy_document()
            n.collect_schema_documents(schemas)
            linked_entities[str(n.id)] = doc
            next_docs.append(doc)
        # include user/group refs of linked docs; do NOT follow their entity links
        more_users, more_groups, _ = _collect_reference_ids(next_docs)
        user_ids |= more_users
        group_ids |= more_groups
        frontier = set()  # depth 1: stop

    users: dict[str, dict] = {}
    if user_ids:
        for u in OpenIDUser.objects.prefetch_related("groups").filter(id__in=user_ids):
            users[str(u.id)] = _serialize_user(u)

    groups: dict[str, dict] = {}
    if group_ids:
        for g in Group.objects.prefetch_related("user_set").filter(id__in=group_ids):
            groups[str(g.id)] = _serialize_group(g)
            # group members referenced only via member_ids still get a user doc
            for u in g.user_set.all():
                users.setdefault(str(u.id), _serialize_user(u))

    return users, groups, linked_entities


def get_udm_type_for_node(node: "UserDefinedModelEntityNode"):
    """Return the UserDefinedModelType for a node (root or submodel)."""
    try:
        return node.userdefinedmodelentity.user_defined_model_type
    except Exception:
        root = node.get_root()
        return root.user_defined_model_type if root else None


def build_entity_document(node: "UserDefinedModelEntityNode") -> dict:
    """Root entity document for the tree containing ``node``."""
    root = node.get_root()
    context = root if root.pk != node.pk else node
    return context.to_policy_document()


def build_policy_input(
    node: "UserDefinedModelEntityNode",
    user: "OpenIDUser",
    action: str,
    *,
    locale: str | None = None,
    changed_fields: dict | None = None,
    old_entity_doc: dict | None = None,
    additional_result: dict | None = None,
    entity_doc: dict | None = None,
    transition: str | None = None,
    field: str | None = None,
    node_id: str | None = None,
    transition_descriptor: dict | None = None,
    candidate_transitions: dict | None = None,
) -> dict:
    """Assemble and validate the input document (contract _input_schema.rego)."""
    udm_type = get_udm_type_for_node(node)
    if udm_type is None:
        raise ValueError("Cannot build policy input for a typeless entity")

    root = node.get_root()
    context = root if root.pk != node.pk else node
    if entity_doc is None:
        entity_doc = context.to_policy_document()
    schemas = context.collect_schema_documents()

    # Contract: old_entity is ALWAYS a document for save/transition/preview.
    # It must reflect PERSISTED state and is therefore always built from the
    # database, never taken from the (possibly caller-supplied) entity_doc.
    # Callers that write inside the open transaction before evaluating
    # (apply_patch, transition with pending edits) MUST snapshot the document
    # before their writes and pass it in — at this point the DB already shows
    # the patched state, so rebuilding here would launder the new state into
    # old_entity. Callers that have not written anything (e.g. the migration
    # save-gate) may omit it and get a fresh DB snapshot.
    if action in ("save", "transition", "preview") and old_entity_doc is None:
        old_entity_doc = context.to_policy_document()

    docs = [entity_doc]
    if old_entity_doc is not None:
        docs.append(old_entity_doc)
    users, groups, linked_entities = build_lookup_maps(docs, schemas)

    user_doc = _serialize_user(user, include_permissions=True)

    input_doc: dict = {
        "input_version": INPUT_VERSION,
        "action": action,
        "locale": locale,
        "type_id": str(udm_type.id),
        "entity": entity_doc,
        "old_entity": old_entity_doc,
        "schemas": schemas,
        "users": users,
        "groups": groups,
        "linked_entities": linked_entities,
        "user": user_doc,
        "changed_fields": changed_fields or {},
        "additional_result": additional_result or {},
    }
    if action == "transition":
        input_doc.update(
            transition=transition,
            field=field,
            node_id=node_id,
            transition_descriptor=transition_descriptor,
        )
    if action == "preview":
        input_doc["candidate_transitions"] = candidate_transitions or {}

    try:
        validate_policy_input(input_doc)
    except PydanticValidationError as exc:
        logger.error("policy input violates contract: %s", exc)
        raise
    return input_doc


# ─── Message validation (§3.1-4) ──────────────────────────────────────────────

class PolicyMessage(BaseModel):
    """The message object policies must emit. ``field_slug`` (dotted path or
    null) is normalized to ``highlight_fields`` for the frontend."""

    model_config = ConfigDict(extra="allow")

    level: str
    text: str
    highlight_fields: list[str] = []

    LEVELS: ClassVar[tuple[str, ...]] = ("critical", "error", "warning", "info", "debug")


def _normalize_policy_messages(messages) -> list[dict]:
    """Validate + normalize raw policy messages; malformed ones are logged and
    dropped instead of being passed through to the API."""
    result = []
    if not isinstance(messages, list):
        return result
    for m in messages:
        if not isinstance(m, dict):
            logger.warning("dropping non-object policy message: %r", m)
            continue
        m = dict(m)
        if "field_slug" in m:
            field_slug = m.pop("field_slug")
            m.setdefault("highlight_fields", [field_slug] if field_slug is not None else [])
        try:
            validated = PolicyMessage.model_validate(m)
        except PydanticValidationError as exc:
            logger.warning("dropping malformed policy message %r: %s", m, exc)
            continue
        if validated.level not in PolicyMessage.LEVELS:
            logger.warning("dropping policy message with unknown level %r", validated.level)
            continue
        result.append(validated.model_dump())
    return result


# ─── Policy evaluation ────────────────────────────────────────────────────────

def evaluate_policy(
    node: "UserDefinedModelEntityNode",
    user: "OpenIDUser",
    action: str,
    **kwargs,
) -> "PolicyEvaluationOutput":
    """Evaluate ``data.udm.result`` for node's UDMType.

    Default-deny: no UDMType, no policies, undefined result, or a contract
    violation all yield the deny output. Field grants are per-node maps; an
    empty map redacts everything (no unrestricted sentinel).
    """
    from userdefinedmodel.actions import PolicyEvaluationOutput

    deny = PolicyEvaluationOutput()

    udm_type = get_udm_type_for_node(node)
    if udm_type is None:
        return deny
    session = get_session_for_type(udm_type)
    if session is None:
        return deny

    try:
        input_doc = build_policy_input(node, user, action, **kwargs)
        gather = logger.isEnabledFor(logging.DEBUG)
        raw_result, prints = session.evaluate(input_doc, "data.udm.result", gather_prints=gather)
        if prints:
            logger.debug("policy prints node=%s action=%s:\n%s", node.id, action, "\n".join(prints))
        if not isinstance(raw_result, dict):
            logger.debug("data.udm.result undefined/non-object for node=%s action=%s", node.id, action)
            return deny
        output = PolicyEvaluationOutput(
            allow=bool(raw_result.get("allow", False)),
            messages=_normalize_policy_messages(raw_result.get("messages", [])),
            viewable_fields=_as_field_map(raw_result.get("viewable_fields")),
            editable_fields=_as_field_map(raw_result.get("editable_fields")),
            valid_transitions=[t for t in (raw_result.get("valid_transitions") or []) if isinstance(t, dict)],
            actions=[a for a in (raw_result.get("actions") or []) if isinstance(a, dict)],
            dashboard_columns=[c for c in (raw_result.get("dashboard_columns") or []) if isinstance(c, dict)],
            additional_result=raw_result.get("additional_result") if isinstance(raw_result.get("additional_result"), dict) else {},
        )
        logger.debug(
            "policy result node=%s action=%s allow=%s messages=%d viewable_nodes=%d editable_nodes=%d",
            node.id, action, output.allow, len(output.messages),
            len(output.viewable_fields), len(output.editable_fields),
        )
        return output
    except Exception as exc:
        logger.exception("Policy evaluation failed: %s", exc)
        return deny


def _as_field_map(value) -> dict[str, list[str]]:
    """Coerce a per-node field grant into {node_id: [slugs]}; anything else
    becomes {} (deny-by-default; the null sentinel is gone)."""
    if not isinstance(value, dict):
        return {}
    out = {}
    for node_id, slugs in value.items():
        if isinstance(slugs, list):
            out[str(node_id)] = [s for s in slugs if isinstance(s, str)]
    return out


def evaluate_view_precheck(
    node: "UserDefinedModelEntityNode",
    user: "OpenIDUser",
    old_entity_doc: dict,
    locale: str | None = None,
) -> tuple[bool, dict]:
    """Evaluate the VIEW policy against the persisted (pre-patch) state.

    Returns ``(allow, additional_result)``. ``additional_result`` is the
    policy-defined carry-over object handed back verbatim as
    ``input.additional_result`` to the subsequent save/transition/preview
    evaluation (replaces the fixed view_was_allowed / old_editable_fields keys).
    """
    output = evaluate_policy(node, user, "view", entity_doc=old_entity_doc, locale=locale)
    additional = dict(output.additional_result)
    return output.allow, additional


def evaluate_type_public_fields(udm_type, user=None, locale: str | None = None) -> tuple[dict, dict]:
    """Evaluate ``data.udm.type_result`` (separate aggregate — §3.1-1).

    Returns (public_type_fields, type_description) where type_description is a
    ``{lang_code: markdown}`` dict. Defaults to empty on undefined/failure.
    """
    session = get_session_for_type(udm_type)
    if session is None:
        return {}, {}
    try:
        input_doc: dict = {
            "input_version": INPUT_VERSION,
            "action": "public_type_fields",
            "locale": locale,
            "type_id": str(udm_type.id),
        }
        if user is not None:
            input_doc["user"] = _serialize_user(user)
        raw, _ = session.evaluate(input_doc, "data.udm.type_result")
        if not isinstance(raw, dict):
            return {}, {}
        fields = raw.get("public_type_fields")
        fields = fields if isinstance(fields, dict) else {}
        desc = raw.get("type_description")
        if isinstance(desc, str):
            descriptions = {"": desc}
        elif isinstance(desc, dict):
            descriptions = {str(k): str(v) for k, v in desc.items() if isinstance(v, str)}
        else:
            descriptions = {}
        return fields, descriptions
    except Exception as exc:
        logger.debug("evaluate_type_public_fields failed: %s", exc, exc_info=True)
        return {}, {}


# ─── Transition candidates (§4 step 2) ────────────────────────────────────────

def _workflow_field_state(node, field_def):
    from userdefinedmodel.models import FieldValue

    fv = (
        FieldValue.objects.filter(node=node, field=field_def, language="")
        .select_related("value_workflow_state")
        .first()
    )
    return fv.value_workflow_state if fv else None


def _state_check_passes(transition: "WorkflowTransition", current_state) -> bool:
    if transition.from_undefined_only:
        return current_state is None
    if transition.from_state_id is not None:
        return current_state is not None and current_state.id == transition.from_state_id
    return True


def build_candidate_transitions(root: "UserDefinedModelEntityNode") -> dict:
    """Enumerate state-valid transition candidates for every node/workflow field
    in the subtree, as full descriptors (contract §4 step 2). No Rego involved."""
    from userdefinedmodel.models import FieldDefinition

    candidates: dict[str, dict] = {}

    def visit(node):
        wf_fields = node.config_version.field_definitions.filter(
            data_type=FieldDefinition.DataType.WORKFLOW
        ).select_related("workflow_version")
        node_entry: dict[str, dict] = {}
        for fd in wf_fields:
            if not fd.workflow_version_id:
                continue
            current = _workflow_field_state(node, fd)
            transitions = {}
            for t in fd.workflow_version.transitions.select_related("from_state", "to_state", "version").all():
                if _state_check_passes(t, current):
                    transitions[t.name] = t.to_descriptor()
            node_entry[fd.slug] = {
                "current_state": current.name if current else None,
                "transitions": transitions,
            }
        if node_entry:
            candidates[str(node.id)] = node_entry
        for child in node.children.all():
            visit(child)

    visit(root)
    return candidates


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
    locale: str | None = None,
    old_entity_doc: dict | None = None,
    _visited: frozenset = frozenset(),
    _depth: int = 0,
    _system: bool = False,
) -> list:
    """
    Execute a named workflow transition on the specified workflow field of `node`.
    Must be called inside an existing transaction.atomic() with the root lock held.

    ``old_entity_doc`` is the persisted (pre-patch) root document; callers that
    apply a patch in the same transaction must snapshot it BEFORE the patch and
    pass it here, so the policy validates the new non-persisted state against
    what was persisted before. When omitted (no patch was applied) the current
    document doubles as the persisted state.

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
        transition = WorkflowTransition.objects.select_related("from_state", "to_state", "version").get(
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

    # Evaluate policy on the (possibly patched, not yet committed) state, with
    # the persisted pre-patch state as context. _system=True skips this check:
    # the call originates from an already-authorized policy action.
    if _system:
        from userdefinedmodel.actions import PolicyEvaluationOutput
        output = PolicyEvaluationOutput(allow=True)
    else:
        if old_entity_doc is None:
            old_entity_doc = build_entity_document(node)
        _view_allowed, additional_result = evaluate_view_precheck(node, user, old_entity_doc, locale=locale)
        output = evaluate_policy(
            node, user, "transition",
            locale=locale,
            transition=name,
            field=field_slug,
            node_id=str(node.id),
            transition_descriptor=transition.to_descriptor(),
            old_entity_doc=old_entity_doc,
            additional_result=additional_result,
        )
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

    # Subtree validation (save-rule floor) — runs AFTER pre-actions on purpose:
    # actions may repair data into a save-valid state for this transition.
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
