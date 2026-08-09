"""Field-binding resolution for sync plugin tab configs
(events-and-sync.md Step 13.2).

A plugin's binding config is an ordered map `remote_property -> source`,
where each source names one of three ways to fill that remote property:
a policy-computed `effective` key (coalesced overrides), a raw stored data
field (no policy involvement needed), or a Jinja template rendered against
`effective`/`entity` (derived text, e.g. a DESCRIPTION assembled from an
HTML/markdown field). Resolution happens once, at `mark_sync` snapshot time
(§4.2) — the resolved map is what gets stored in `synced_payload` and later
diffed by `recompute_staleness`; plugins never see field slugs or effective
keys again once the payload is on the item.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator


class BindingSource(BaseModel):
    """Exactly one of `effective` / `field` / `template` must be set."""

    model_config = ConfigDict(extra="forbid")

    effective: str | None = None
    field: str | None = None
    template: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "BindingSource":
        set_count = sum(v is not None for v in (self.effective, self.field, self.template))
        if set_count != 1:
            raise ValueError("BindingSource must set exactly one of effective/field/template")
        return self


def resolve_binding_value(source: BindingSource, *, entity, effective: dict) -> Any:
    if source.effective is not None:
        return effective.get(source.effective)
    if source.field is not None:
        fv = entity.get_field_value(source.field)
        return fv.get_value() if fv else None
    if source.template is not None:
        from userdefinedmodel.mailtemplates import jsonify_context, render_string

        context = jsonify_context({"effective": effective, "entity": {"id": str(entity.id)}})
        return render_string(source.template, context, autoescape=False)
    raise AssertionError("unreachable: BindingSource always sets exactly one source")


def resolve_bindings(bindings: dict[str, BindingSource | dict], *, entity, effective: dict) -> dict[str, Any]:
    """Resolve every `remote_property -> source` entry in `bindings` against
    `entity`/`effective`, returning the remote-property map to store as
    `synced_payload`."""
    resolved = {}
    for remote_property, source in bindings.items():
        parsed = source if isinstance(source, BindingSource) else BindingSource.model_validate(source)
        resolved[remote_property] = resolve_binding_value(parsed, entity=entity, effective=effective)
    return resolved


class SubmodelSpec(BaseModel):
    """Fan out over a `submodel_list` field's children — e.g.
    `{"submodel": "timeslots", "start": "start", "end": "end"}`
    (events-and-sync.md §13.3). `start`/`end` are child data-field slugs;
    `end` is optional (a point-in-time slot)."""

    model_config = ConfigDict(extra="forbid")

    submodel: str
    start: str
    end: str | None = None


def resolve_submodel_slots(spec: SubmodelSpec, *, entity) -> list[dict[str, Any]]:
    """Enumerate `entity`'s `spec.submodel` children, returning one
    `{"child_id": str, "start": iso-string | None, "end": iso-string | None}`
    per child — `child_id` is stable across edits of a slot (§13.3's "entity
    uid + child node id"), so plugins can key a per-slot remote uid off it."""
    from userdefinedmodel.models import UserDefinedModelEntityNode

    children = UserDefinedModelEntityNode.objects.filter(
        parent_node_id=entity.id, parent_field__slug=spec.submodel,
    ).order_by("created_at")
    slots = []
    for child in children:
        start_fv = child.get_field_value(spec.start)
        start_val = start_fv.get_value() if start_fv else None
        end_val = None
        if spec.end:
            end_fv = child.get_field_value(spec.end)
            end_val = end_fv.get_value() if end_fv else None
        slots.append({
            "child_id": str(child.id),
            "start": start_val.isoformat() if hasattr(start_val, "isoformat") else start_val,
            "end": end_val.isoformat() if hasattr(end_val, "isoformat") else end_val,
        })
    return slots


def resolve_deep(value: Any, *, entity, effective: dict) -> Any:
    """Recursively resolve any `BindingSource`- or `SubmodelSpec`-shaped
    dict found anywhere inside `value` (nested in dicts/lists), leaving
    every other key/value as a literal. Lets a plugin's tab config schema
    nest binding sources (or a submodel fan-out spec) inside richer
    structures without sync_core needing to know that schema's shape — a
    dict is treated as one of these iff it validates as one (`extra=
    "forbid"`), otherwise it's structural and its values are resolved
    individually."""
    if isinstance(value, dict):
        try:
            source = BindingSource.model_validate(value)
        except ValidationError:
            pass
        else:
            return resolve_binding_value(source, entity=entity, effective=effective)
        try:
            spec = SubmodelSpec.model_validate(value)
        except ValidationError:
            return {k: resolve_deep(v, entity=entity, effective=effective) for k, v in value.items()}
        return resolve_submodel_slots(spec, entity=entity)
    if isinstance(value, list):
        return [resolve_deep(v, entity=entity, effective=effective) for v in value]
    return value
