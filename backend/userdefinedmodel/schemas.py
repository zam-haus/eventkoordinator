"""
Pydantic/Django-Ninja schemas for the userdefinedmodel API (/api/udm/).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Optional

from ninja import Schema
from pydantic import Field, field_validator, model_validator

# ─── Cardinality / length caps ────────────────────────────────────────────────

_MAX_SLUG_LEN = 80
_MAX_LABEL_LEN = 200
_MAX_HELP_TEXT_LEN = 2_000
_MAX_DESCRIPTION_LEN = 5_000
_MAX_NOTES_LEN = 2_000
_MAX_ADMIN_LABEL_LEN = 200
_MAX_LANG_CODE_LEN = 10
_MAX_STATE_NAME_LEN = 100
_MAX_TRANS_NAME_LEN = 100
_MAX_MIME_LEN = 100
_MAX_REGEX_LEN = 500
_MAX_FAIL_MSG_LEN = 200
_MAX_SORT_ORDER = 32_767

_MAX_FIELDS = 200
_MAX_LANGUAGES = 50
_MAX_CHOICES = 500
_MAX_CHOICE_LEN = 200
_MAX_STATES = 100
_MAX_TRANSITIONS = 200
_MAX_GROUP_IDS = 100
_MAX_CHANGED_FIELDS = 200
_MAX_MAPPING_ENTRIES = 300

# ─── Reusable annotated types ─────────────────────────────────────────────────

Slug = Annotated[str, Field(min_length=1, max_length=_MAX_SLUG_LEN, pattern=r"^[a-z][a-z0-9_-]*$")]
LangCode = Annotated[str, Field(min_length=2, max_length=_MAX_LANG_CODE_LEN, pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]+)*$")]
Label = Annotated[str, Field(min_length=1, max_length=_MAX_LABEL_LEN)]
HelpText = Annotated[str, Field(max_length=_MAX_HELP_TEXT_LEN)]

LocalizedLabel = Annotated[dict[LangCode, Label], Field(min_length=1, max_length=_MAX_LANGUAGES)]
LocalizedHelpText = Annotated[dict[LangCode, HelpText], Field(max_length=_MAX_LANGUAGES)]

# ─── Enums ────────────────────────────────────────────────────────────────────

class DataType(str, Enum):
    TEXT_SHORT = "text_short"; TEXT_LONG = "text_long"
    TEXT_MARKDOWN = "text_markdown"; TEXT_RICHTEXT = "text_richtext"
    INTEGER = "integer"; FLOAT = "float"; BOOLEAN = "boolean"
    DATE = "date"; TIME = "time"; DATETIME = "datetime"
    SELECT_SINGLE = "select_single"; SELECT_MULTI = "select_multi"
    IMAGE = "image"; FILE = "file"
    USER_SELECT = "user_select"; USER_SELECT_MULTI = "user_select_multi"
    GROUP_SELECT = "group_select"; GROUP_SELECT_MULTI = "group_select_multi"
    SUBMODEL_SELECT = "submodel_select"; SUBMODEL_LIST = "submodel_list"
    ENTITY_SELECT = "entity_select"; ENTITY_SELECT_MULTI = "entity_select_multi"
    SLUG_ID = "slug_id"
    WORKFLOW = "workflow"
    # Structural / layout types
    TAB_CONTAINER = "tab_container"
    TAB = "tab"
    SAVE_BUTTON = "save_button"
    HSTACK = "hstack"
    HSTACK_GROUP = "hstack_group"
    TAB_PREV = "tab_prev"
    TAB_NEXT = "tab_next"

STRUCTURAL_DATA_TYPES = frozenset({
    DataType.TAB_CONTAINER,
    DataType.TAB,
    DataType.SAVE_BUTTON,
    DataType.HSTACK,
    DataType.HSTACK_GROUP,
    DataType.TAB_PREV,
    DataType.TAB_NEXT,
})


class ConfigVersionStatus(str, Enum):
    DRAFT = "draft"; PUBLISHED = "published"; ARCHIVED = "archived"


class MigrationAction(str, Enum):
    MAP = "map"; DISCARD = "discard"; OVERFLOW = "overflow"


class BulkMigrationStatus(str, Enum):
    DRAFT = "draft"; RUNNING = "running"; DONE = "done"; PARTIAL = "partial"


# ─── TypeConfig models ────────────────────────────────────────────────────────

class TextTypeConfig(Schema):
    renderer: Optional[Literal["markdown_wysiwyg", "markdown_preview", "plaintext"]] = None
    model_config = {"extra": "forbid"}


class NumberTypeConfig(Schema):
    decimal_places: Optional[int] = Field(None, ge=0, le=10)
    model_config = {"extra": "forbid"}


class SelectTypeConfig(Schema):
    choices: list[Annotated[str, Field(min_length=1, max_length=_MAX_CHOICE_LEN)]] = Field(
        ..., min_length=1, max_length=_MAX_CHOICES
    )
    model_config = {"extra": "forbid"}


class UserGroupTypeConfig(Schema):
    limit_to_group_ids: Optional[list[int]] = Field(None, max_length=_MAX_GROUP_IDS)
    default_current_user: bool = False
    model_config = {"extra": "forbid"}


class EntitySelectTypeConfig(Schema):
    limit_to_type_ids: Optional[list[int]] = Field(None, max_length=100)
    display_field_slug: Optional[Annotated[str, Field(max_length=_MAX_SLUG_LEN)]] = None
    model_config = {"extra": "forbid"}


class SubmodelTypeConfig(Schema):
    renderer: Optional[Literal["table", "list"]] = None
    model_config = {"extra": "forbid"}


_MAX_SLUG_ID_PREFIX_LEN = 200

class SlugIdTypeConfig(Schema):
    prefix: Annotated[str, Field(min_length=1, max_length=_MAX_SLUG_ID_PREFIX_LEN, pattern=r"^[A-Z][A-Z0-9_]*$")]
    model_config = {"extra": "forbid"}


class WorkflowTypeConfig(Schema):
    model_config = {"extra": "forbid"}


class TabContainerTypeConfig(Schema):
    title: Optional[Annotated[str, Field(max_length=_MAX_LABEL_LEN)]] = None
    model_config = {"extra": "forbid"}


class TabTypeConfig(Schema):
    title: Optional[Annotated[str, Field(max_length=_MAX_LABEL_LEN)]] = None
    model_config = {"extra": "forbid"}


class SaveButtonTypeConfig(Schema):
    label: Optional[Annotated[str, Field(max_length=_MAX_LABEL_LEN)]] = None
    variant: Optional[Literal["primary", "success"]] = None
    model_config = {"extra": "forbid"}


class HStackTypeConfig(Schema):
    model_config = {"extra": "forbid"}


class HStackGroupTypeConfig(Schema):
    align: Optional[Literal["left", "center", "right"]] = "left"
    model_config = {"extra": "forbid"}


class TabNavTypeConfig(Schema):
    label: Optional[Annotated[str, Field(max_length=_MAX_LABEL_LEN)]] = None
    model_config = {"extra": "forbid"}


_TYPE_CONFIG_CLS: dict[DataType, type[Schema] | None] = {
    DataType.TEXT_SHORT: TextTypeConfig, DataType.TEXT_LONG: TextTypeConfig,
    DataType.TEXT_MARKDOWN: TextTypeConfig, DataType.TEXT_RICHTEXT: TextTypeConfig,
    DataType.INTEGER: NumberTypeConfig, DataType.FLOAT: NumberTypeConfig,
    DataType.BOOLEAN: None, DataType.DATE: None,
    DataType.TIME: None, DataType.DATETIME: None,
    DataType.SELECT_SINGLE: SelectTypeConfig, DataType.SELECT_MULTI: SelectTypeConfig,
    DataType.IMAGE: None, DataType.FILE: None,
    DataType.USER_SELECT: UserGroupTypeConfig, DataType.USER_SELECT_MULTI: UserGroupTypeConfig,
    DataType.GROUP_SELECT: UserGroupTypeConfig, DataType.GROUP_SELECT_MULTI: UserGroupTypeConfig,
    DataType.SUBMODEL_SELECT: SubmodelTypeConfig, DataType.SUBMODEL_LIST: SubmodelTypeConfig,
    DataType.ENTITY_SELECT: EntitySelectTypeConfig, DataType.ENTITY_SELECT_MULTI: EntitySelectTypeConfig,
    DataType.SLUG_ID: SlugIdTypeConfig,
    DataType.WORKFLOW: WorkflowTypeConfig,
    DataType.TAB_CONTAINER: TabContainerTypeConfig,
    DataType.TAB: TabTypeConfig,
    DataType.SAVE_BUTTON: SaveButtonTypeConfig,
    DataType.HSTACK: HStackTypeConfig,
    DataType.HSTACK_GROUP: HStackGroupTypeConfig,
    DataType.TAB_PREV: TabNavTypeConfig,
    DataType.TAB_NEXT: TabNavTypeConfig,
}

# ─── FieldDefinition schemas ──────────────────────────────────────────────────

class FieldDefinitionIn(Schema):
    slug: Slug
    data_type: DataType
    sort_order: int = Field(0, ge=0, le=_MAX_SORT_ORDER)
    is_localized: bool = False
    is_preview: bool = False
    labels: Optional[LocalizedLabel] = None
    help_texts: LocalizedHelpText = Field(default_factory=dict)
    type_config: dict[str, Any] = Field(default_factory=dict)
    default: Optional[Any] = None
    submodel_config_version_id: Optional[uuid.UUID] = None
    workflow_version_id: Optional[uuid.UUID] = None
    parent_slug: Optional[Annotated[str, Field(max_length=_MAX_SLUG_LEN, pattern=r"^[a-z][a-z0-9_-]*$")]] = None
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_type_config(self) -> "FieldDefinitionIn":
        is_structural = self.data_type in STRUCTURAL_DATA_TYPES
        if not is_structural and not self.labels:
            raise ValueError("labels is required for data fields")
        cls = _TYPE_CONFIG_CLS.get(self.data_type)
        if cls is None:
            if self.type_config:
                raise ValueError(f"{self.data_type} does not accept type_config")
        else:
            cls.model_validate(self.type_config)
        submodel_types = {DataType.SUBMODEL_SELECT, DataType.SUBMODEL_LIST}
        if self.data_type in submodel_types and self.submodel_config_version_id is None:
            raise ValueError("submodel_config_version_id required for submodel types")
        if self.data_type not in submodel_types and self.submodel_config_version_id is not None:
            raise ValueError("submodel_config_version_id must be null for non-submodel types")
        if self.data_type == DataType.WORKFLOW and self.workflow_version_id is None:
            raise ValueError("workflow_version_id required for workflow type")
        if self.data_type != DataType.WORKFLOW and self.workflow_version_id is not None:
            raise ValueError("workflow_version_id must be null for non-workflow types")
        return self


class FieldDefinitionOut(Schema):
    id: uuid.UUID
    slug: str
    data_type: str
    sort_order: int
    is_localized: bool
    is_preview: bool
    label: dict[str, str]
    help_text: dict[str, str]
    type_config: dict[str, Any]
    submodel_config: Optional["ConfigVersionOut"] = None
    workflow_version: Optional["WorkflowVersionOut"] = None
    default: Optional[Any] = None
    parent_slug: Optional[str] = None

# ─── Languages and FieldConfig schemas ───────────────────────────────────────

class ConfigLanguageIn(Schema):
    code: LangCode
    label: Label
    is_default: bool = False
    sort_order: int = Field(0, ge=0, le=_MAX_SORT_ORDER)
    model_config = {"extra": "forbid"}


class ConfigLanguageOut(Schema):
    code: str; label: str; is_default: bool; sort_order: int


class FieldConfigCreateIn(Schema):
    name: Annotated[str, Field(min_length=1, max_length=_MAX_LABEL_LEN)]
    description: Annotated[str, Field(max_length=_MAX_DESCRIPTION_LEN)] = ""
    languages: list[ConfigLanguageIn] = Field(..., min_length=1, max_length=_MAX_LANGUAGES)
    model_config = {"extra": "forbid"}

    @field_validator("languages")
    @classmethod
    def exactly_one_default(cls, langs: list[ConfigLanguageIn]) -> list[ConfigLanguageIn]:
        if sum(1 for l in langs if l.is_default) != 1:
            raise ValueError("exactly one language must have is_default=True")
        return langs


class FieldConfigUpdateIn(Schema):
    name: Optional[Annotated[str, Field(min_length=1, max_length=_MAX_LABEL_LEN)]] = None
    description: Optional[Annotated[str, Field(max_length=_MAX_DESCRIPTION_LEN)]] = None
    model_config = {"extra": "forbid"}


class FieldConfigOut(Schema):
    id: uuid.UUID; name: str; description: str
    created_at: datetime
    last_published_at: Optional[datetime]
    version_count: int
    stale_entity_count: int
    entity_count: int
    published_submodel_usage_count: int
    type_ids: list[uuid.UUID]
    languages: list[ConfigLanguageOut]

# ─── Workflow schemas ─────────────────────────────────────────────────────────

class WorkflowStateIn(Schema):
    name: Annotated[str, Field(min_length=1, max_length=_MAX_STATE_NAME_LEN, pattern=r"^[a-z][a-z0-9_-]*$")]
    # When renaming, previous_name is the old slug so the backend can locate the existing row.
    previous_name: Optional[Annotated[str, Field(min_length=1, max_length=_MAX_STATE_NAME_LEN, pattern=r"^[a-z][a-z0-9_-]*$")]] = None
    label: LocalizedLabel
    is_initial: bool = False
    position_x: float = 0.0
    position_y: float = 0.0
    background_color: Annotated[str, Field(max_length=7, pattern=r"^#[0-9a-fA-F]{6}$")] = "#ffffff"
    model_config = {"extra": "forbid"}


class WorkflowTransitionIn(Schema):
    name: Annotated[str, Field(min_length=1, max_length=_MAX_TRANS_NAME_LEN, pattern=r"^[a-z][a-z0-9_-]*$")]
    label: LocalizedLabel
    from_state: Optional[Annotated[str, Field(max_length=_MAX_STATE_NAME_LEN)]] = None
    # True: only fires when current state is undefined (null). from_state must be null.
    # False (default): fires from the named from_state, or from any state if from_state is null.
    from_undefined_only: bool = False
    to_state: Annotated[str, Field(min_length=1, max_length=_MAX_STATE_NAME_LEN)]
    source_handle: Annotated[str, Field(max_length=30)] = ""
    target_handle: Annotated[str, Field(max_length=30)] = ""
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_from_state_constraints(self) -> "WorkflowTransitionIn":
        if self.from_undefined_only and self.from_state is not None:
            raise ValueError("from_undefined_only=True requires from_state to be null")
        return self


class WorkflowDefinitionIn(Schema):
    name: Annotated[str, Field(min_length=1, max_length=_MAX_LABEL_LEN)]
    description: Annotated[str, Field(max_length=_MAX_DESCRIPTION_LEN)] = ""
    states: list[WorkflowStateIn] = Field(..., min_length=1, max_length=_MAX_STATES)
    transitions: list[WorkflowTransitionIn] = Field(default_factory=list, max_length=_MAX_TRANSITIONS)
    model_config = {"extra": "forbid"}

    @field_validator("states")
    @classmethod
    def exactly_one_initial(cls, states: list[WorkflowStateIn]) -> list[WorkflowStateIn]:
        if sum(1 for s in states if s.is_initial) != 1:
            raise ValueError("exactly one state must have is_initial=True")
        return states


class WorkflowStateOut(Schema):
    name: str; label: dict[str, str]; is_initial: bool
    position_x: float; position_y: float
    background_color: str = "#ffffff"
    text_color: str = "#000000"


class WorkflowTransitionOut(Schema):
    name: str; label: dict[str, str]
    from_state: Optional[str]; from_undefined_only: bool; to_state: str
    source_handle: str; target_handle: str


class WorkflowOut(Schema):
    initial_state: str
    states: list[WorkflowStateOut]
    transitions: list[WorkflowTransitionOut]


class WorkflowVersionOut(Schema):
    """Workflow version content (states, transitions) with its own id."""
    id: uuid.UUID
    status: str
    states: list[WorkflowStateOut]
    transitions: list[WorkflowTransitionOut]
    virtual_node_positions: dict[str, Any] = Field(default_factory=dict)


class WorkflowDefinitionOut(Schema):
    id: uuid.UUID
    name: str
    description: str
    initial_state: Optional[str]
    states: list[WorkflowStateOut]
    transitions: list[WorkflowTransitionOut]
    virtual_node_positions: dict[str, Any] = Field(default_factory=dict)
    draft_version_id: Optional[uuid.UUID] = None
    published_version_id: Optional[uuid.UUID] = None


class WorkflowCreateIn(Schema):
    name: Annotated[str, Field(min_length=1, max_length=_MAX_LABEL_LEN)]
    description: Annotated[str, Field(max_length=_MAX_DESCRIPTION_LEN)] = ""
    states: list[WorkflowStateIn] = Field(..., min_length=1, max_length=_MAX_STATES)
    transitions: list[WorkflowTransitionIn] = Field(default_factory=list, max_length=_MAX_TRANSITIONS)
    virtual_node_positions: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}

    @field_validator("states")
    @classmethod
    def exactly_one_initial(cls, states: list[WorkflowStateIn]) -> list[WorkflowStateIn]:
        if sum(1 for s in states if s.is_initial) != 1:
            raise ValueError("exactly one state must have is_initial=True")
        return states


class StateMigrationIn(Schema):
    from_state: Annotated[str, Field(min_length=1, max_length=_MAX_STATE_NAME_LEN)]
    to_state: Annotated[str, Field(min_length=1, max_length=_MAX_STATE_NAME_LEN)]
    model_config = {"extra": "forbid"}


class WorkflowUpdateIn(Schema):
    name: Optional[Annotated[str, Field(min_length=1, max_length=_MAX_LABEL_LEN)]] = None
    description: Optional[Annotated[str, Field(max_length=_MAX_DESCRIPTION_LEN)]] = None
    states: Optional[list[WorkflowStateIn]] = Field(None, max_length=_MAX_STATES)
    transitions: Optional[list[WorkflowTransitionIn]] = Field(None, max_length=_MAX_TRANSITIONS)
    virtual_node_positions: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}

# ─── ConfigVersion schemas ────────────────────────────────────────────────────

class ConfigDraftIn(Schema):
    notes: Annotated[str, Field(max_length=_MAX_NOTES_LEN)] = ""
    fields: list[FieldDefinitionIn] = Field(..., min_length=0, max_length=_MAX_FIELDS)
    model_config = {"extra": "forbid"}

    @field_validator("fields")
    @classmethod
    def unique_slugs(cls, fields: list[FieldDefinitionIn]) -> list[FieldDefinitionIn]:
        seen: set[str] = set()
        for f in fields:
            if f.slug in seen:
                raise ValueError(f"duplicate slug '{f.slug}'")
            seen.add(f.slug)
        return fields


class ConfigVersionOut(Schema):
    version_id: uuid.UUID
    status: str
    notes: str
    published_at: Optional[str]
    languages: list[ConfigLanguageOut]
    fields: list[FieldDefinitionOut]


FieldDefinitionOut.model_rebuild()
WorkflowDefinitionOut.model_rebuild()
WorkflowVersionOut.model_rebuild()

# ─── Entity schemas ───────────────────────────────────────────────────────────

class EntityCreateIn(Schema):
    user_defined_model_type_id: uuid.UUID
    model_config = {"extra": "forbid"}


class EntityPatchIn(Schema):
    changed_fields: dict[
        Annotated[str, Field(min_length=1, max_length=_MAX_SLUG_LEN)],
        Any,
    ] = Field(..., max_length=_MAX_CHANGED_FIELDS)
    model_config = {"extra": "forbid"}


class TransitionIn(Schema):
    field: Slug
    transition: Annotated[str, Field(min_length=1, max_length=_MAX_TRANS_NAME_LEN)]
    changed_fields: dict = Field(default_factory=dict)
    model_config = {"extra": "forbid"}


class SubmodelOpKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class SubmodelOperationIn(Schema):
    op: SubmodelOpKind
    id: Optional[uuid.UUID] = None
    fields: dict[
        Annotated[str, Field(min_length=1, max_length=_MAX_SLUG_LEN)],
        Any,
    ] = Field(default_factory=dict, max_length=_MAX_CHANGED_FIELDS)
    sort_order: Optional[int] = Field(None, ge=0, le=_MAX_SORT_ORDER)
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_op_constraints(self) -> "SubmodelOperationIn":
        if self.op in (SubmodelOpKind.UPDATE, SubmodelOpKind.DELETE) and self.id is None:
            raise ValueError(f"id is required for op='{self.op}'")
        if self.op == SubmodelOpKind.DELETE and self.fields:
            raise ValueError("fields must be absent for op='delete'")
        return self


SubmodelListPatch = Annotated[list[SubmodelOperationIn], Field(max_length=_MAX_FIELDS)]


class UserRefOut(Schema):
    id: uuid.UUID; display_name: str


class FieldValueOut(Schema):
    field_slug: str; data_type: str
    value: Any
    language: str = ""


class EntityOut(Schema):
    id: uuid.UUID
    config_version_id: uuid.UUID
    user_defined_model_type_id: Optional[uuid.UUID]
    field_values: list[FieldValueOut]
    children: dict[str, list[Any]]
    overflow_data: dict[str, Any]
    created_at: str; updated_at: str
    viewable_fields: Optional[list[str]] = None
    editable_fields: list[str] = []
    policy_messages: list[Any] = []

# ─── Edit history schemas ─────────────────────────────────────────────────────

class FieldEditOut(Schema):
    change_kind: str
    field_slug: Optional[str] = None
    field_label: Optional[str] = None
    language: str = ""
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    old_file_name: Optional[str] = None
    new_file_name: Optional[str] = None
    old_file_url: Optional[str] = None
    new_file_url: Optional[str] = None
    affected_node_id: Optional[uuid.UUID] = None


class EditGroupOut(Schema):
    id: uuid.UUID; saved_at: str
    saved_by: Optional[UserRefOut]
    node_id: uuid.UUID; node_type: str
    edits: list[FieldEditOut]


class EditHistoryOut(Schema):
    count: int; next: Optional[str]; results: list[EditGroupOut]

# ─── Migration schemas ────────────────────────────────────────────────────────

class MigrationFieldMappingIn(Schema):
    source_field_slug: Slug
    action: MigrationAction
    target_field_slug: Optional[Slug] = None
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def target_required_for_map(self) -> "MigrationFieldMappingIn":
        if self.action == MigrationAction.MAP and not self.target_field_slug:
            raise ValueError("target_field_slug required when action is 'map'")
        return self


class MigrationExecuteIn(Schema):
    migration_id: uuid.UUID
    confirmed: Literal[True]
    field_mappings: list[MigrationFieldMappingIn] = Field(..., max_length=_MAX_MAPPING_ENTRIES)
    model_config = {"extra": "forbid"}


class MigrationPreviewFieldOut(Schema):
    source_slug: str; source_data_type: str
    suggested_action: MigrationAction
    suggested_target_slug: Optional[str]
    conflict_reason: Optional[str]


class MigrationPreviewOut(Schema):
    migration_id: uuid.UUID
    source_version_id: uuid.UUID; target_version_id: uuid.UUID
    field_previews: list[MigrationPreviewFieldOut]


class SubmodelMigrationIn(Schema):
    """Field mappings for child nodes under a single SUBMODEL_* field when the submodel version changed."""
    source_parent_field_slug: Slug
    target_submodel_version_id: uuid.UUID
    field_mappings: list[MigrationFieldMappingIn] = Field(default_factory=list, max_length=_MAX_MAPPING_ENTRIES)
    model_config = {"extra": "forbid"}


class WorkflowFieldStateMappingIn(Schema):
    """Explicit state name overrides for a single workflow field during bulk migration."""
    field_slug: Slug
    state_mappings: list[StateMigrationIn] = Field(default_factory=list, max_length=_MAX_STATES)
    model_config = {"extra": "forbid"}


class BulkMigrationCreateIn(Schema):
    source_version_id: uuid.UUID; target_version_id: uuid.UUID
    user_defined_model_type_filter_id: Optional[uuid.UUID] = None
    field_mappings: list[MigrationFieldMappingIn] = Field(..., max_length=_MAX_MAPPING_ENTRIES)
    submodel_mappings: list[SubmodelMigrationIn] = Field(default_factory=list, max_length=_MAX_FIELDS)
    workflow_state_mappings: list[WorkflowFieldStateMappingIn] = Field(default_factory=list, max_length=_MAX_FIELDS)
    model_config = {"extra": "forbid"}


class BulkMigrationOut(Schema):
    id: uuid.UUID; status: BulkMigrationStatus
    source_version_id: uuid.UUID; target_version_id: uuid.UUID
    user_defined_model_type_filter_id: Optional[uuid.UUID]
    total_entities: int; done_entities: int; failed_entities: int
    executed_at: Optional[str]

# ─── Staging file and autocomplete schemas ────────────────────────────────────

class StagingFileOut(Schema):
    staging_id: uuid.UUID
    original_name: str; mime_type: str; size_bytes: int; expires_at: str


class UserAutocompleteItem(Schema):
    id: uuid.UUID; display_name: str


class GroupAutocompleteItem(Schema):
    id: int; name: str


class EntityAutocompleteItem(Schema):
    id: uuid.UUID
    display: str
    type_id: Optional[uuid.UUID]

# ─── Standard error schemas ───────────────────────────────────────────────────

class ConcurrentEditError(Schema):
    error: Literal["concurrent_edit"]
    retry_after_ms: int = 500


class FieldErrorsOut(Schema):
    errors: dict[str, list[str]]


class EditingNotAllowedError(Schema):
    error: Literal["editing_not_allowed_in_state"]
    current_state: str

# ─── Policy schemas ───────────────────────────────────────────────────────────

class PolicyAction(str, Enum):
    BROWSE = "browse"
    VIEW = "view"
    EDIT = "edit"
    CREATE = "create"
    SAVE = "save"
    DELETE = "delete"
    TRANSITION = "transition"


class MessageLevel(str, Enum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"


LocalizedMessage = Annotated[
    dict[LangCode, Annotated[str, Field(max_length=2_000)]],
    Field(min_length=1, max_length=_MAX_LANGUAGES)
]


class PolicyMessage(Schema):
    level: MessageLevel
    message: LocalizedMessage
    field_slug: Optional[str] = None


class PolicyOutput(Schema):
    allow: bool
    messages: list[PolicyMessage] = []
    viewable_fields: list[str] = []
    editable_fields: list[str] = []


class PolicyCreateIn(Schema):
    slug: Slug
    source: Annotated[str, Field(min_length=1, max_length=500_000)]
    model_config = {"extra": "forbid"}


class PolicyUpdateIn(Schema):
    source: Annotated[str, Field(min_length=1, max_length=500_000)]
    model_config = {"extra": "forbid"}


class PolicyOut(Schema):
    slug: str
    source: str


class PolicyAssignIn(Schema):
    policy_slug: Slug
    sort_order: int = Field(0, ge=0, le=_MAX_SORT_ORDER)
    model_config = {"extra": "forbid"}


# ─── UDMType schemas ──────────────────────────────────────────────────────────

class UDMTypeOut(Schema):
    id: uuid.UUID
    name: str
    label: str
    field_config_id: Optional[uuid.UUID]


class UDMTypeCreateIn(Schema):
    name: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=200)


class UDMTypeUpdateIn(Schema):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    label: Optional[str] = Field(default=None, max_length=200)


class TypePublicFieldsOut(Schema):
    descriptions: dict[str, str]  # lang_code → markdown; "" = language-neutral fallback


class PolicyEvalOut(Schema):
    input_document: dict[str, Any]
    policies: list[dict[str, str]]   # [{"slug": ..., "source": ...}]
    output: dict[str, Any]           # allow, messages, viewable_fields, editable_fields
    full_document: Optional[dict[str, Any]] = None  # full data.udm namespace
    error: Optional[str] = None
    rule_errors: list[str] = []      # per-rule evaluation errors
    prints: list[str] = []           # stdout lines emitted by print() in Rego
    coverage: list[dict] = []        # per-file coverage: {path, covered, not_covered}


# ─── Draft-as-input export schemas ───────────────────────────────────────────

class FieldDefinitionDraftOut(Schema):
    """FieldDefinition serialised in the same shape that FieldDefinitionIn accepts,
    so the output can be fed back into PUT .../draft/ without modification."""
    slug: str
    data_type: str
    sort_order: int
    is_localized: bool
    is_preview: bool
    labels: Optional[dict[str, str]] = None
    help_texts: dict[str, str]
    type_config: dict[str, Any]
    default: Optional[Any] = None
    submodel_config_version_id: Optional[uuid.UUID] = None
    workflow_version_id: Optional[uuid.UUID] = None
    parent_slug: Optional[str] = None


class ConfigDraftExportOut(Schema):
    """ConfigVersion serialised in ConfigDraftIn shape for round-trip export."""
    notes: str
    fields: list[FieldDefinitionDraftOut]


# ─── Bundle export / import schemas ──────────────────────────────────────────

class BundleWorkflowOut(Schema):
    id: uuid.UUID
    name: str
    description: str
    states: list[WorkflowStateOut]
    transitions: list[WorkflowTransitionOut]
    virtual_node_positions: dict[str, Any]


class BundleFieldConfigOut(Schema):
    id: uuid.UUID
    name: str
    description: str
    languages: list[ConfigLanguageOut]
    draft: ConfigDraftExportOut


class BundleUDMTypeOut(Schema):
    id: uuid.UUID
    name: str
    field_config_id: Optional[uuid.UUID]
    policy_slugs: list[str]


class BundleExportOut(Schema):
    version: int = 1
    scope_type_ids: list[uuid.UUID]
    udm_types: list[BundleUDMTypeOut]
    field_configs: list[BundleFieldConfigOut]
    workflows: list[BundleWorkflowOut]
    policies: list[PolicyOut]


class BundleImportIn(Schema):
    rego_source: Annotated[str, Field(min_length=1, max_length=2_000_000)]
    scope_type_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)
    policy_slug: Optional[Slug] = None
    model_config = {"extra": "forbid"}


class BundleExportIn(Schema):
    scope_type_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)
    model_config = {"extra": "forbid"}
