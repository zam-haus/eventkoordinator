"""sync_pretix's type-editor tab: field binding (events-and-sync.md §13.2,
extended by §14 for dynamic parent-event resolution + item/variation
bindings).

Target selection stays on the shared `sync_targets` tab (unchanged); this tab
says what each remote subevent property is filled from, which Pretix event a
type's entities create subevents under, and which ticket products/variations
get price overrides and quota membership.
"""
from pydantic import BaseModel, ConfigDict, Field

from sync_core.binding import BindingSource

#: The remote subevent properties sync_pretix's push() knows how to fill.
REMOTE_PROPERTIES = ["title", "start", "end", "locale", "max_participants"]


class PretixItemBinding(BaseModel):
    """One ticket product (optionally one of its variations) this type's
    entities push. `item`/`variation` accept either a Pretix numeric ID or a
    display name (matched case-insensitively at push time against the live
    Pretix item list — same convention as the legacy `ticket_product_*_id`
    association fields). Every entry here is a required price override
    (there's no "no override" state — bind a source that resolves to the
    item's normal price if you don't want to change it) and is always part
    of the subevent's shared quota — the item bindings list *is* the quota
    membership, not an opt-in per entry. The quota's capacity (size) comes
    from the `max_participants` field binding above, not from here."""

    model_config = ConfigDict(extra="forbid")

    item: str = Field(..., title="Item", description="Pretix item ID or name.")
    variation: str | None = Field(
        default=None, title="Variation",
        description="Pretix item variation ID or name, if this product has variations.",
    )
    price: BindingSource = Field(
        ..., title="Price override",
        description="Required source for this item/variation's price override on the subevent.",
    )


class PretixBindingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: dict[str, BindingSource] = Field(
        default_factory=dict,
        title="Field bindings",
        description=f"remote_property -> source, for {REMOTE_PROPERTIES}.",
    )
    parent_event: BindingSource = Field(
        ..., title="Parent event",
        description=(
            "Required — resolves the Pretix event slug this type's entities create "
            "subevents under. Not a per-type admin-configured association: it's "
            "computed per entity (e.g. from a policy effective key, a data field, or "
            "a template), so no manual event-slug assignment is needed anywhere. "
            "Pinned into PretixSyncItem.remote_identity at first successful push — a "
            "later change here does not move an already-created subevent "
            "(events-and-sync.md §14); it instead surfaces as a compute_drift entry."
        ),
    )
    items: list[PretixItemBinding] = Field(
        default_factory=list,
        title="Item/variation bindings",
        description="Ticket products (and optional variations) to push price overrides/quota membership for.",
    )


def register() -> None:
    from userdefinedmodel.type_editor_tabs import register_type_editor_tab

    register_type_editor_tab("sync_pretix", "Pretix Sync", PretixBindingConfig)
