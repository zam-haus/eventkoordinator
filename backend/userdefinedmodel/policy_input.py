"""Pydantic schema for the target policy input contract.

Mirrors documentation/configuration/policies/_input_schema.rego (the Rego-side
contract); the two are kept in lockstep via INPUT_VERSION and validated
against each other by documentation/configuration/policies/check_input_schema.py,
which runs every Rego-generated example document through
:func:`validate_policy_input`.

Deliberately Django-free so it can be imported without settings configured.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, model_validator

INPUT_VERSION = 1

ENTITY_ACTIONS = ("view", "browse", "create", "save", "delete", "transition", "preview")

# old_entity is null exactly for these; see _input_schema.rego valid_old_entity.
NO_OLD_ENTITY_ACTIONS = frozenset({"view", "browse", "delete", "create"})


class FieldEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_type: str
    localized: bool
    # scalar, list, {"<lang>": scalar} when localized, or None when unset
    value: Any


class NodeDocument(BaseModel):
    model_config = ConfigDict(extra="allow")  # overflow_data, timestamps, …

    id: str
    schema_id: str
    fields: dict[str, FieldEntry]
    children: dict[str, list["NodeDocument"]]


class SchemaFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    data_type: str


class SchemaDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    fields: dict[str, SchemaFieldDefinition]
    properties: dict[str, Any]


class GroupRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str


class UserDocument(BaseModel):
    model_config = ConfigDict(extra="allow")  # email, phone_number, permissions, …

    id: str
    username: str
    groups: list[GroupRef]
    # session-scoped superuser sudo toggle; only ever True on input.user
    sudo: bool = False


class GroupDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    member_ids: list[str]


class TransitionDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_state: Optional[str]
    to_state: str
    from_undefined_only: bool
    properties: dict[str, Any]


class WorkflowFieldCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_state: Optional[str]
    transitions: dict[str, TransitionDescriptor]


class PublicTypeFieldsInput(BaseModel):
    """Minimal input for the type-metadata action (reads data.udm.type_result)."""

    model_config = ConfigDict(extra="forbid")

    input_version: Literal[1]
    action: Literal["public_type_fields"]
    locale: Optional[str]  # null exactly when the system calls itself
    type_id: str
    user: UserDocument


class EntityActionInput(BaseModel):
    """Input for every entity action; one evaluation on the ROOT document."""

    model_config = ConfigDict(extra="forbid")

    input_version: Literal[1]
    action: Literal["view", "browse", "create", "save", "delete", "transition", "preview"]
    locale: Optional[str]  # null exactly when the system calls itself
    type_id: str  # never null: typeless entities are denied before Rego runs
    entity: NodeDocument
    old_entity: Optional[NodeDocument]
    schemas: dict[str, SchemaDocument]
    users: dict[str, UserDocument]
    groups: dict[str, GroupDocument]
    linked_entities: dict[str, NodeDocument]  # entity_select targets, one deep
    user: UserDocument
    changed_fields: dict[str, Any]
    additional_result: dict[str, Any]  # VIEW pre-check carry-over

    # transition action only:
    transition: Optional[str] = None
    field: Optional[str] = None
    node_id: Optional[str] = None
    transition_descriptor: Optional[TransitionDescriptor] = None

    # preview action only:
    candidate_transitions: Optional[dict[str, dict[str, WorkflowFieldCandidates]]] = None

    @model_validator(mode="after")
    def _check_action_shape(self) -> "EntityActionInput":
        if self.action in NO_OLD_ENTITY_ACTIONS:
            if self.old_entity is not None:
                raise ValueError(f"old_entity must be null for action {self.action!r}")
        elif self.old_entity is None:
            raise ValueError(f"old_entity is required for action {self.action!r}")

        transition_keys = (self.transition, self.field, self.node_id, self.transition_descriptor)
        if self.action == "transition":
            if any(v is None for v in transition_keys):
                raise ValueError("transition/field/node_id/transition_descriptor are required for action 'transition'")
        elif any(v is not None for v in transition_keys):
            raise ValueError(f"transition keys are not allowed for action {self.action!r}")

        if self.action == "preview":
            if self.candidate_transitions is None:
                raise ValueError("candidate_transitions is required for action 'preview'")
        elif self.candidate_transitions is not None:
            raise ValueError(f"candidate_transitions is not allowed for action {self.action!r}")

        for node in _walk_nodes(self.entity):
            if node.schema_id not in self.schemas:
                raise ValueError(f"node {node.id!r} references unknown schema_id {node.schema_id!r}")
        return self


def _walk_nodes(node: NodeDocument):
    yield node
    for siblings in node.children.values():
        for child in siblings:
            yield from _walk_nodes(child)


PolicyInput = Union[PublicTypeFieldsInput, EntityActionInput]


def validate_policy_input(doc: dict) -> PolicyInput:
    """Validate a raw input document; raises pydantic.ValidationError."""
    if doc.get("action") == "public_type_fields":
        return PublicTypeFieldsInput.model_validate(doc)
    return EntityActionInput.model_validate(doc)
