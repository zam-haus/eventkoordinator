"""sync_core's own type-editor tab registration (events-and-sync.md §5).

Demonstrates the registry end-to-end even though no concrete sync plugin
(sync_ical/sync_caldav/sync_pretix port, sync_webhook) exists yet: which
SyncBaseTarget keys are bound to a UDM type is itself useful config,
independent of any specific plugin's binding details.
"""
from pydantic import BaseModel, ConfigDict, Field


class SyncTargetsTabConfig(BaseModel):
    """Which concrete SyncBaseTarget keys this type may mark_sync against."""

    model_config = ConfigDict(extra="forbid")

    target_keys: list[str] = Field(default_factory=list)


def register() -> None:
    from userdefinedmodel.type_editor_tabs import register_type_editor_tab

    register_type_editor_tab("sync_targets", "Sync Targets", SyncTargetsTabConfig)
