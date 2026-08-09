"""sync_ical's type-editor tab: field binding (events-and-sync.md §13.2),
extended by §13.3 for per-timeslot VEVENT fan-out.

Target selection stays on the shared `sync_targets` tab (unchanged); this tab
says what each remote VEVENT property is filled from, and optionally which
submodel to fan out over — one remote VEVENT per child instead of one for
the whole entity.
"""
from pydantic import BaseModel, ConfigDict, Field

from sync_core.binding import BindingSource, SubmodelSpec

#: The VEVENT properties sync_ical's push() knows how to fill.
REMOTE_PROPERTIES = ["SUMMARY", "LOCATION", "DESCRIPTION", "DTSTART", "DTEND"]


class IcalBindingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: dict[str, BindingSource] = Field(
        default_factory=dict,
        title="Field bindings",
        description=f"remote_property -> source, for {REMOTE_PROPERTIES}.",
    )
    submodel: SubmodelSpec | None = Field(
        default=None, title="Fan out over submodel",
        description=(
            "Push one remote VEVENT per child of this submodel_list field instead of "
            "one for the whole entity — e.g. {\"submodel\": \"timeslots\", \"start\": "
            "\"start\", \"end\": \"end\"} for an Event's Timeslot children. DTSTART/DTEND "
            "come from each child; SUMMARY/LOCATION/DESCRIPTION stay shared from the "
            "bindings above. Each VEVENT's UID is entity id + child id, stable across "
            "edits — moving a slot updates its VEVENT, deleting one removes it "
            "(events-and-sync.md §13.3)."
        ),
    )


def register() -> None:
    from userdefinedmodel.type_editor_tabs import register_type_editor_tab

    register_type_editor_tab("sync_ical", "iCal Sync", IcalBindingConfig)
