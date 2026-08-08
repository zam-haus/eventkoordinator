"""Plugin type-editor tab registry (events-and-sync.md §5).

Each sync plugin — or any app that needs its own per-type configuration
surface — registers a tab descriptor at Django startup (AppConfig.ready):

    from userdefinedmodel.type_editor_tabs import register_type_editor_tab

    register_type_editor_tab("sync_caldav", "CalDAV Sync", CalDAVTypeBindingSchema)

The type-config API (api_type_editor_tabs.py) exposes the registered tab
list and per-(ConfigVersion, tab) CRUD for the config blob, validated
against the plugin's own pydantic schema. The blob lives on the
ConfigVersion, so it rolls with config versions like everything else in the
form-tree/field editor.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class TabDescriptor:
    id: str
    label: str
    config_schema: type[BaseModel]


_TAB_REGISTRY: dict[str, TabDescriptor] = {}


def register_type_editor_tab(id: str, label: str, config_schema: type[BaseModel]) -> None:
    """Register a type-editor tab. Raises ValueError on a duplicate id —
    each id must be unique across the entire Django project, same
    convention as userdefinedmodel.actions.policy_action."""
    if id in _TAB_REGISTRY:
        raise ValueError(
            f"Type editor tab {id!r} is already registered. "
            "Each id must be unique across the entire Django project."
        )
    _TAB_REGISTRY[id] = TabDescriptor(id=id, label=label, config_schema=config_schema)


def get_registered_tabs() -> list[TabDescriptor]:
    return list(_TAB_REGISTRY.values())


def get_tab(tab_id: str) -> TabDescriptor | None:
    return _TAB_REGISTRY.get(tab_id)


def clear_registry() -> None:
    """Test helper — mirrors the pattern tests already use for
    userdefinedmodel.actions._action_registry (snapshot/restore in
    setUp/tearDown, not calling this directly in application code)."""
    _TAB_REGISTRY.clear()
