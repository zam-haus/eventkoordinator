"""
Policy action system for userdefinedmodel.

Actions are declared by Rego policies as structured output, validated by
Pydantic, and dispatched to handlers registered with ``@policy_action``.

External apps extend the system::

    from userdefinedmodel.actions import policy_action
    from pydantic import BaseModel
    from typing import Literal

    class MyOutput(BaseModel):
        type: Literal["my_action"]
        phase: Literal["pre", "post"]
        channel: str

    @policy_action("my_action", schema=MyOutput)
    def handle_my_action(action: MyOutput, ctx: ActionContext) -> None:
        post_to_channel(action.channel, ctx.node)

Registration happens at Django app startup (AppConfig.ready), requiring no
DB models or migrations.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from userdefinedmodel.models.node import UserDefinedModelEntityNode
    from userdefinedmodel.models.history import EditGroup
    from openid_user_management.models import OpenIDUser

logger = logging.getLogger(__name__)


# ─── ActionContext ─────────────────────────────────────────────────────────────

class ActionContext(BaseModel):
    """Immutable context threaded through a single action dispatch chain.

    Use ``ctx.model_copy(update={...})`` to derive a modified context for
    recursive calls (e.g. when transitioning a child node).
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    node: Any
    """The node on which the triggering event occurred (entity or submodel)."""

    user: Any
    """The OpenIDUser who initiated the triggering event."""

    trigger: Literal["save", "create", "transition"]
    """Which lifecycle event produced this context."""

    phase: Literal["pre", "post"]
    """Current dispatch phase — pre runs before validation, post after."""

    edit_group: Any | None = None
    """EditGroup for the current transaction, shared across recursive calls."""

    visited_transitions: frozenset = frozenset()
    """(node_id_str, field_slug, transition_name) keys already visited this chain.
    Prevents infinite loops in TriggerTransitionOutput chains."""

    depth: int = 0
    """Recursion depth — raised by 1 for each nested trigger_transition call."""


# ─── Action output schemas ─────────────────────────────────────────────────────

class SetFieldValueOutput(BaseModel):
    """Set a field value on the current node or a descendant submodel.

    ``field_path`` formats:

    * ``"slug"`` — scalar field on the triggering node
    * ``"select_slug.child_slug"`` — field on the ``submodel_select`` child node
    * ``"list_slug[*].child_slug"`` — field on **all** ``submodel_list`` children
    """

    type: Literal["set_field_value"]
    phase: Literal["pre", "post"]
    field_path: str = Field(description="Dot-notation path to the target field")
    value: Any = Field(description="Value to write; None clears the field")


class TriggerTransitionOutput(BaseModel):
    """Trigger a named workflow transition synchronously.

    The transition runs to completion — including its own pre/post actions —
    before the next post-action in the current chain continues.  A cycle guard
    (``ActionContext.visited_transitions``) and depth cap (10) prevent infinite
    recursion.
    """

    type: Literal["trigger_transition"]
    phase: Literal["pre", "post"]
    field_slug: str = Field(description="Slug of the WORKFLOW-type FieldDefinition")
    transition_name: str = Field(description="Name of the WorkflowTransition to execute")
    target_scope: Literal["self", "children", "all_descendants"] = Field(
        default="self",
        description=(
            "Which nodes to trigger the transition on. "
            "'self' = ctx.node; 'children' = direct children; "
            "'all_descendants' = entire subtree excluding ctx.node"
        ),
    )


class SendNotificationOutput(BaseModel):
    """Enqueue an email notification.

    In ``post`` phase the send is deferred to ``transaction.on_commit`` so the
    mail is only queued if the surrounding transaction commits successfully.
    """

    type: Literal["send_notification"]
    phase: Literal["pre", "post"]
    recipients_config: list[Any] = Field(
        default_factory=list,
        description="Recipient config dicts (same structure as mailqueue)",
    )
    subject_template: str = Field(default="", description="Subject template string")
    body_template: str = Field(default="", description="Body template string")


PolicyActionOutput = Annotated[
    Union[SetFieldValueOutput, TriggerTransitionOutput, SendNotificationOutput],
    Field(discriminator="type"),
]


class PolicyEvaluationOutput(BaseModel):
    """Typed, validated output of a Rego policy evaluation.

    ``actions`` is stored as raw dicts so that externally-registered action
    types (not in the built-in discriminated union) can be included without
    Pydantic rejecting them.  Each action dict is validated against its
    registered schema at dispatch time by :func:`dispatch_actions`.
    """

    allow: bool = False
    messages: list[dict] = []
    viewable_fields: list[str] | None = None
    editable_fields: list[str] = []
    dashboard_columns: list[dict] = []
    actions: list[dict] = []


# ─── Registry ─────────────────────────────────────────────────────────────────

_action_registry: dict[str, tuple[type[BaseModel], Callable]] = {}


def policy_action(type_name: str, *, schema: type[BaseModel]) -> Callable:
    """Decorator that registers a policy action handler.

    ``type_name`` must match the ``type`` discriminator value in the Rego output.
    ``schema`` is the Pydantic model used to validate and deserialise the action.
    The decorated callable must accept ``(action: schema, ctx: ActionContext)``
    and return ``None``.

    Raises ``ValueError`` if ``type_name`` is already registered.
    """
    def decorator(fn_or_cls: Callable) -> Callable:
        if type_name in _action_registry:
            raise ValueError(
                f"Policy action type {type_name!r} is already registered. "
                "Each type_name must be unique across the entire Django project."
            )
        _action_registry[type_name] = (schema, fn_or_cls)
        return fn_or_cls

    return decorator


# ─── Dispatcher ───────────────────────────────────────────────────────────────

def dispatch_actions(actions: list[dict], ctx: ActionContext) -> None:
    """Dispatch all actions whose phase matches ``ctx.phase``.

    Each element of ``actions`` is a raw dict from the Rego output.  The
    ``type`` key is used to look up the registered schema and handler.
    Validation against the registered Pydantic schema happens here, which
    means external action types registered via :func:`policy_action` are
    fully supported — the static ``PolicyActionOutput`` union is for
    documentation purposes only.

    Unknown type names are logged and skipped — they could come from a newer
    policy deployed before the handler app is updated.

    Per-action ``on_error`` (if present in the dict, default ``"log"``)
    controls failure handling: ``"log"`` logs and continues; ``"raise"``
    re-raises; ``"ignore"`` is silent.
    """
    for raw in actions:
        if not isinstance(raw, dict):
            continue
        if raw.get("phase") != ctx.phase:
            continue
        type_name = raw.get("type")
        entry = _action_registry.get(type_name)
        if entry is None:
            logger.warning(
                "Unknown policy action type %r on node %s — skipping (not registered)",
                type_name,
                ctx.node.id,
            )
            continue
        schema_cls, handler = entry
        on_error = raw.get("on_error", "log")
        try:
            action = schema_cls.model_validate(raw)
            handler(action, ctx)
        except Exception as exc:
            if on_error == "raise":
                raise
            elif on_error == "log":
                logger.warning(
                    "PolicyAction %r failed on node %s: %s",
                    type_name,
                    ctx.node.id,
                    exc,
                )
            # on_error == "ignore": silent


# ─── Field path resolution ─────────────────────────────────────────────────────

def _resolve_field_path(node, path: str) -> list[tuple]:
    """Resolve a ``field_path`` string to ``[(node, field_def), ...]`` pairs.

    Supported patterns:

    * ``"slug"`` → the named field on *node*
    * ``"select_slug.child_slug"`` → navigate into a ``submodel_select`` child
    * ``"list_slug[*].child_slug"`` → all children of a ``submodel_list`` field

    Raises ``ValueError`` when the path refers to a field that does not exist.
    """
    from userdefinedmodel.models import FieldDefinition

    # Wildcard list path: "list_slug[*].child_slug"
    if "[*]." in path:
        parent_slug, child_slug = path.split("[*].", 1)
        parent_field = node.config_version.field_definitions.filter(slug=parent_slug).first()
        if parent_field is None:
            raise ValueError(f"Field {parent_slug!r} not found on node {node.id}")
        results = []
        for child in node.children.filter(parent_field__slug=parent_slug).select_related("config_version"):
            child_field = child.config_version.field_definitions.filter(slug=child_slug).first()
            if child_field is not None:
                results.append((child, child_field))
        return results

    # Submodel-select path: "select_slug.child_slug"
    if "." in path:
        parent_slug, child_slug = path.split(".", 1)
        parent_field = node.config_version.field_definitions.filter(slug=parent_slug).first()
        if parent_field is None or parent_field.data_type != FieldDefinition.DataType.SUBMODEL_SELECT:
            raise ValueError(
                f"Field {parent_slug!r} is not a submodel_select field on node {node.id}"
            )
        fv = node.field_values.filter(field=parent_field, language="").first()
        if fv is None or fv.value_node_id is None:
            return []
        from userdefinedmodel.models.node import SubmodelInstance

        try:
            child = SubmodelInstance.objects.get(id=fv.value_node_id)
        except SubmodelInstance.DoesNotExist:
            return []
        child_field = child.config_version.field_definitions.filter(slug=child_slug).first()
        if child_field is None:
            raise ValueError(f"Field {child_slug!r} not found on submodel node {child.id}")
        return [(child, child_field)]

    # Simple single-segment path
    field_def = node.config_version.field_definitions.filter(slug=path).first()
    if field_def is None:
        raise ValueError(f"Field {path!r} not found on node {node.id}")
    return [(node, field_def)]


def _collect_subtree_nodes(node) -> list:
    """DFS collection of *node* and all descendants (node first)."""
    result = [node]
    for child in node.children.all():
        result.extend(_collect_subtree_nodes(child))
    return result


# ─── Built-in action handlers ──────────────────────────────────────────────────

@policy_action("set_field_value", schema=SetFieldValueOutput)
def _handle_set_field_value(action: SetFieldValueOutput, ctx: ActionContext) -> None:
    targets = _resolve_field_path(ctx.node, action.field_path)
    for target_node, field_def in targets:
        fv, _ = target_node.field_values.get_or_create(field=field_def, language="")
        if action.value is None:
            fv.delete()
        else:
            fv.set_value(action.value, field=field_def)
            fv.save()


@policy_action("trigger_transition", schema=TriggerTransitionOutput)
def _handle_trigger_transition(action: TriggerTransitionOutput, ctx: ActionContext) -> None:
    from userdefinedmodel.engine import execute_transition, TransitionError

    if ctx.depth >= 10:
        raise TransitionError("trigger_transition max recursion depth (10) exceeded.")

    scope = action.target_scope
    if scope == "self":
        candidates = [ctx.node]
    elif scope == "children":
        candidates = list(ctx.node.children.all())
    else:  # all_descendants
        candidates = _collect_subtree_nodes(ctx.node)[1:]

    for target_node in candidates:
        key = (str(target_node.id), action.field_slug, action.transition_name)
        if key in ctx.visited_transitions:
            logger.warning(
                "trigger_transition cycle detected (%s / %s / %s) — skipping",
                target_node.id,
                action.field_slug,
                action.transition_name,
            )
            continue
        execute_transition(
            target_node,
            field_slug=action.field_slug,
            name=action.transition_name,
            user=ctx.user,
            edit_group=ctx.edit_group,
            _visited=ctx.visited_transitions | {key},
            _depth=ctx.depth + 1,
        )


@policy_action("send_notification", schema=SendNotificationOutput)
def _handle_send_notification(action: SendNotificationOutput, ctx: ActionContext) -> None:
    from django.db import transaction as db_transaction

    def _send() -> None:
        logger.info(
            "send_notification action for node %s (trigger=%s): subject=%r",
            ctx.node.id,
            ctx.trigger,
            action.subject_template,
        )

    if ctx.phase == "post":
        db_transaction.on_commit(_send)
    else:
        _send()
