"""Reverse lookup of entities referencing a given entity via entity_select /
entity_select_multi fields. Shared by delete protection (api_entities.py),
the backlink_list form element endpoint, and the engine's backlink_inputs
resolution."""
from __future__ import annotations

import dataclasses

from userdefinedmodel.models.config import FieldDefinition
from userdefinedmodel.models.node import FieldValue, UserDefinedModelEntity


@dataclasses.dataclass(frozen=True)
class Backlink:
    entity: UserDefinedModelEntity
    type_id: str | None
    field_slug: str


def find_backlinks(target_id) -> list[Backlink]:
    """All (entity, type, field_slug) references to `target_id`.

    entity_select stores a real FK (value_node); entity_select_multi stores
    raw id strings in value_json, so it needs a separate containment query.
    A referencing node may be a submodel instance, not the root entity, so
    each match is resolved up to its root UserDefinedModelEntity.
    """
    target_id = str(target_id)

    single_qs = FieldValue.objects.filter(
        field__data_type=FieldDefinition.DataType.ENTITY_SELECT,
        value_node_id=target_id,
    ).select_related("field", "node")

    multi_qs = FieldValue.objects.filter(
        field__data_type=FieldDefinition.DataType.ENTITY_SELECT_MULTI,
        value_json__contains=[target_id],
    ).select_related("field", "node")

    backlinks = []
    for fv in list(single_qs) + list(multi_qs):
        root = fv.node.get_root()
        backlinks.append(Backlink(
            entity=root,
            type_id=str(root.user_defined_model_type_id) if root.user_defined_model_type_id else None,
            field_slug=fv.field.slug,
        ))
    return backlinks


def backlink_summary(target_id) -> dict:
    """Policy-input-shaped summary for the delete-policy input:
    {"count": n, "by_type_field": [{"type_id", "field_slug", "count"}]}"""
    backlinks = find_backlinks(target_id)
    counts: dict[tuple[str | None, str], int] = {}
    for bl in backlinks:
        key = (bl.type_id, bl.field_slug)
        counts[key] = counts.get(key, 0) + 1
    return {
        "count": len(backlinks),
        "by_type_field": [
            {"type_id": type_id, "field_slug": field_slug, "count": count}
            for (type_id, field_slug), count in sorted(
                counts.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])
            )
        ],
    }
