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

    When ``target_scope`` is ``"children"`` or ``"all_descendants"``, use
    ``target_parent_field`` to restrict to children attached to a specific
    ``submodel_list`` field (e.g. ``"reviews"``).  Without it, all children
    of the triggering node are targeted, including those that don't have the
    workflow field — those are silently skipped.
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
    target_parent_field: str | None = Field(
        default=None,
        description=(
            "When set, only children attached via this parent_field slug are targeted. "
            "Use this to restrict 'children'/'all_descendants' to a specific submodel_list "
            "(e.g. 'reviews') and avoid hitting unrelated submodels."
        ),
    )


class SendNotificationOutput(BaseModel):
    """Enqueue an email notification.

    In ``post`` phase the send is deferred to ``transaction.on_commit`` so the
    mail is only queued if the surrounding transaction commits successfully.

    **Template-based sending** (recommended to stay within Rego's 1 024-char
    line limit): set ``template_name`` to a base path (e.g.
    ``"proposals/submit"``) and the handler will render
    ``{template_name}.txt.j2`` and ``{template_name}.html.j2`` via Django's
    template loader.  Template context: ``node``, ``user``, ``trigger``.

    **Inline sending**: leave ``template_name`` empty and provide
    ``body_text`` / ``body_html`` directly.
    """

    type: Literal["send_notification"]
    phase: Literal["pre", "post"]
    subject: str = Field(default="", description="Email subject line")
    template_name: str = Field(
        default="",
        description=(
            "Base template path (without suffix).  "
            "Renders <name>.txt.j2 and <name>.html.j2."
        ),
    )
    body_text: str = Field(default="", description="Plain-text body (used when template_name is empty)")
    body_html: str = Field(default="", description="HTML body (used when template_name is empty)")
    recipient_field: str | None = Field(
        default=None,
        description=(
            "Slug of a user_select field on the triggering node whose user's "
            "email address is the primary recipient.  Stacked with extra_recipients."
        ),
    )
    extra_recipients: list[str] = Field(
        default_factory=list,
        description="Additional explicit email addresses to send to.",
    )


class CreateSubmodelItemOutput(BaseModel):
    """Create a new item in a ``submodel_list`` field, optionally pre-seeded with field values.

    Field values in ``fields`` may use these special interpolation markers
    (replaced before the submodel item is written):

    * ``"$$user.id"``       → ``str(ctx.user.id)``
    * ``"$$user.email"``    → ``ctx.user.email``
    * ``"$$user.username"`` → ``ctx.user.username``
    """

    type: Literal["create_submodel_item"]
    phase: Literal["pre", "post"]
    field_slug: str = Field(description="Slug of the submodel_list FieldDefinition to append to")
    fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Initial field values for the new submodel item (supports $$ markers)",
    )


PolicyActionOutput = Annotated[
    Union[
        SetFieldValueOutput,
        TriggerTransitionOutput,
        SendNotificationOutput,
        CreateSubmodelItemOutput,
    ],
    Field(discriminator="type"),
]


class PolicyEvaluationOutput(BaseModel):
    """Typed, validated output of a Rego policy evaluation (data.udm.result).

    Field grants are PER-NODE maps ``{node_id: [slugs]}`` covering the whole
    model tree; empty means fully redacted (deny-by-default — there is no
    unrestricted sentinel). ``additional_result`` is the policy-defined
    carry-over from the VIEW pre-check to save/transition/preview inputs.

    ``actions`` is stored as raw dicts so that externally-registered action
    types (not in the built-in discriminated union) can be included without
    Pydantic rejecting them.  Each action dict is validated against its
    registered schema at dispatch time by :func:`dispatch_actions`.
    """

    allow: bool = False
    messages: list[dict] = []
    viewable_fields: dict[str, list[str]] = {}
    editable_fields: dict[str, list[str]] = {}
    valid_transitions: list[dict] = []
    # §6 submodel operation grants (deny-by-default, like the field maps):
    # creatable_submodels: {parent_id: {field_slug: {"viewable": [...], "editable": [...]}}}
    # — a present field-slug key grants creation; its value is the field grant
    # for the not-yet-saved item form.
    deletable_nodes: list[str] = []
    creatable_submodels: dict[str, dict[str, dict]] = {}
    dashboard_columns: list[dict] = []
    actions: list[dict] = []
    additional_result: dict = {}


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

def _action_history_value(raw: dict, error: Exception | None = None) -> dict:
    """Build the new_value dict written to FieldEdit for a policy action.

    Large inline body fields are stripped; everything else is kept so the
    history record is useful for auditing without consuming excessive space.
    """
    STRIP_KEYS = {"body_text", "body_html"}
    value = {k: v for k, v in raw.items() if k not in STRIP_KEYS}
    if error is not None:
        value["_error"] = str(error)
    return value


def _ensure_edit_group(ctx: ActionContext):
    """Return the edit_group on *ctx*, creating one lazily if absent.

    Returns the (possibly new) EditGroup.  The returned group is **not**
    written back to *ctx* — callers use the return value directly.
    """
    if ctx.edit_group is not None:
        return ctx.edit_group

    from userdefinedmodel.models.history import EditGroup

    try:
        root_entity = ctx.node.userdefinedmodelentity
    except Exception:
        root_entity = ctx.node.get_root()

    return EditGroup.objects.create(
        node=ctx.node,
        root_entity=root_entity,
        saved_by=ctx.user,
    )


def _log_action(
    edit_group,
    ctx: ActionContext,
    raw: dict,
    error: Exception | None = None,
) -> None:
    """Write a POLICY_PRE_ACTION or POLICY_POST_ACTION FieldEdit row."""
    from userdefinedmodel.models.history import FieldEdit

    kind = (
        FieldEdit.ChangeKind.POLICY_PRE_ACTION
        if ctx.phase == "pre"
        else FieldEdit.ChangeKind.POLICY_POST_ACTION
    )
    FieldEdit.objects.create(
        group=edit_group,
        change_kind=kind,
        affected_node=ctx.node,
        new_value=_action_history_value(raw, error=error),
    )


def dispatch_actions(actions: list[dict], ctx: ActionContext) -> None:
    """Dispatch all actions whose phase matches ``ctx.phase``.

    Each element of ``actions`` is a raw dict from the Rego output.  The
    ``type`` key is used to look up the registered schema and handler.
    Validation against the registered Pydantic schema happens here, which
    means external action types registered via :func:`policy_action` are
    fully supported — the static ``PolicyActionOutput`` union is for
    documentation purposes only.

    Each successfully dispatched action — and each action that fails with
    ``on_error="log"`` — is written to the edit history as a
    ``POLICY_PRE_ACTION`` or ``POLICY_POST_ACTION`` FieldEdit row on the
    same EditGroup as the triggering event.  Failed actions record the error
    message in ``new_value._error``.  Unknown action types are logged and
    skipped without a history entry.

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
            edit_group = _ensure_edit_group(ctx)
        except Exception as eg_exc:
            logger.debug(
                "PolicyAction %r on node %s: skipping history logging — %s",
                type_name,
                ctx.node.id,
                eg_exc,
            )
            edit_group = None

        try:
            action = schema_cls.model_validate(raw)
            handler(action, ctx)
            if edit_group is not None:
                _log_action(edit_group, ctx, raw)
        except Exception as exc:
            if edit_group is not None:
                _log_action(edit_group, ctx, raw, error=exc)
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
        qs = ctx.node.children.all()
        if action.target_parent_field:
            qs = qs.filter(parent_field__slug=action.target_parent_field)
        candidates = list(qs)
    else:  # all_descendants
        all_nodes = _collect_subtree_nodes(ctx.node)[1:]
        if action.target_parent_field:
            all_nodes = [
                n for n in all_nodes
                if getattr(getattr(n, "parent_field", None), "slug", None) == action.target_parent_field
            ]
        candidates = all_nodes

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
        try:
            execute_transition(
                target_node,
                field_slug=action.field_slug,
                name=action.transition_name,
                user=ctx.user,
                edit_group=ctx.edit_group,
                _visited=ctx.visited_transitions | {key},
                _depth=ctx.depth + 1,
                _system=True,
            )
        except TransitionError as exc:
            if exc.http_status == 404:
                # Field or transition doesn't exist on this node type — skip silently.
                logger.debug(
                    "trigger_transition: skipping node %s — %s",
                    target_node.id,
                    exc,
                )
            else:
                raise


@policy_action("send_notification", schema=SendNotificationOutput)
def _handle_send_notification(action: SendNotificationOutput, ctx: ActionContext) -> None:
    from django.core.mail import send_mail
    from django.template.loader import render_to_string

    # ── Resolve recipients (inside the transaction so errors reach history) ──
    recipient_emails: list[str] = list(action.extra_recipients)
    if action.recipient_field:
        fv = ctx.node.field_values.filter(field__slug=action.recipient_field, language="").first()
        if fv and fv.value_user_id:
            from openid_user_management.models import OpenIDUser
            u = OpenIDUser.objects.get(id=fv.value_user_id)
            if u.email:
                recipient_emails.insert(0, u.email)

    if not recipient_emails:
        raise ValueError(
            f"send_notification for node {ctx.node.id}: "
            f"no recipients resolved (recipient_field={action.recipient_field!r}, "
            f"extra_recipients={action.extra_recipients!r})"
        )

    # ── Render templates (inside the transaction so errors reach history) ──
    ctx_dict = {
        "node": ctx.node,
        "user": ctx.user,
        "trigger": ctx.trigger,
        # Convenience dict so templates can write {{ fields.title }} instead
        # of calling node.get_field_value("title").get_value()
        "fields": {
            fv.field.slug: fv.get_value()
            for fv in ctx.node.field_values.select_related("field").all()
        },
    }
    if action.template_name:
        body_text = render_to_string(f"{action.template_name}.txt.j2", ctx_dict)
        body_html = render_to_string(f"{action.template_name}.html.j2", ctx_dict)
    else:
        body_text = action.body_text
        body_html = action.body_html or None

    # ── Enqueue inside the transaction ───────────────────────────────────────────
    # MailQueueBackend writes a MailQueueEntry DB row — no SMTP happens here.
    # Running inside the same atomic() guarantees that the queue entry and the
    # transition state change are committed together or rolled back together.
    send_mail(
        subject=action.subject,
        message=body_text,
        html_message=body_html,
        from_email=None,  # uses settings.DEFAULT_FROM_EMAIL
        recipient_list=recipient_emails,
        fail_silently=False,
    )


_USER_INTERPOLATIONS: dict[str, Any] = {}  # populated lazily to avoid import cycles

def _interpolate_fields(fields: dict[str, Any], user) -> dict[str, Any]:
    """Replace $$user.* markers in field values with live user data."""
    markers = {
        "$$user.id": str(user.id),
        "$$user.email": user.email or "",
        "$$user.username": user.username or "",
    }
    result = {}
    for k, v in fields.items():
        result[k] = markers.get(v, v) if isinstance(v, str) else v
    return result


@policy_action("create_submodel_item", schema=CreateSubmodelItemOutput)
def _handle_create_submodel_item(action: CreateSubmodelItemOutput, ctx: ActionContext) -> None:
    from userdefinedmodel.models import FieldDefinition
    from userdefinedmodel.models.node import SubmodelInstance
    from userdefinedmodel.writer import apply_patch

    field_def = ctx.node.config_version.field_definitions.filter(
        slug=action.field_slug,
        data_type=FieldDefinition.DataType.SUBMODEL_LIST,
    ).first()
    if field_def is None:
        raise ValueError(
            f"create_submodel_item: field {action.field_slug!r} is not a "
            f"submodel_list field on node {ctx.node.id}"
        )
    if field_def.submodel_config_id is None:
        raise ValueError(
            f"create_submodel_item: field {action.field_slug!r} has no submodel config"
        )

    max_order = ctx.node.children.filter(parent_field=field_def).aggregate(
        m=__import__("django.db.models", fromlist=["Max"]).Max("submodelinstance__sort_order")
    )["m"] or 0

    child = SubmodelInstance.objects.create(
        config_version=field_def.submodel_config,
        parent_node=ctx.node,
        parent_field=field_def,
        sort_order=max_order + 1,
    )
    child.materialize_defaults()
    child.materialize_user_defaults(ctx.user)

    seeded = _interpolate_fields(action.fields, ctx.user)
    if seeded:
        apply_patch(child, seeded, ctx.user, edit_group=ctx.edit_group)
