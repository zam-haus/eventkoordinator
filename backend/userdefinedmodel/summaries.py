"""Human-readable node summaries for edit history.

A node's summary is built from the ``is_preview`` fields of its config version
— the same fields the submodel cards use as their collapsed label. History
needs the summary *as it was before* a save, so :func:`prime_node_summaries`
snapshots the whole subtree at the start of a patch and
:class:`~userdefinedmodel.models.history.FieldEdit` reads that snapshot when a
row is written (after the values have already changed).

Parts are stored per source field (``[{"slug": ..., "text": ...}]``) so the
history endpoint can drop the parts whose field the viewer may not see.
"""

import contextvars
import logging

logger = logging.getLogger(__name__)

# {node_id (str): [{"slug": str, "text": str}]} — pre-write snapshot for the
# node subtree currently being patched. Set for the duration of one top-level
# apply_patch; a ContextVar so concurrent requests never share it.
_summary_snapshot: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "udm_summary_snapshot", default=None
)


def _default_language(config_version) -> str:
    try:
        for lang in config_version.config.languages.all():
            if lang.is_default:
                return lang.code
    except Exception:
        logger.debug("no languages for config version %s", config_version.pk, exc_info=True)
    return ""


def _render(fd, value) -> str | None:
    if value is None or value == "":
        return None
    if hasattr(value, "original_name"):  # FileAttachment
        return value.original_name
    if fd.data_type == "slug_id":
        prefix = (fd.type_config or {}).get("prefix", "")
        try:
            return f"{prefix}-{int(value)}" if prefix else str(value)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def compute_node_summary_parts(node) -> list[dict]:
    """Build the preview parts for ``node`` from its current stored values."""
    config_version = node.config_version
    if config_version is None:
        return []

    preview_slugs = set(
        config_version.form_elements.filter(
            is_preview=True, element_type="field"
        ).values_list("bindings__data_field__slug", flat=True)
    )
    preview_slugs.discard(None)
    if not preview_slugs:
        return []

    preview_fields = [
        fd for fd in config_version.field_definitions.all() if fd.slug in preview_slugs
    ]
    if not preview_fields:
        return []

    default_lang = _default_language(config_version)
    fv_map = {}
    for fv in node.field_values.all():
        fv_map[(fv.field_id, fv.language)] = fv

    parts = []
    for fd in preview_fields:
        if fd.is_localized:
            fv = fv_map.get((fd.id, default_lang)) or next(
                (fv_map[k] for k in fv_map if k[0] == fd.id), None
            )
        else:
            fv = fv_map.get((fd.id, ""))
        if fv is None:
            continue
        try:
            text = _render(fd, fv.get_value(field=fd))
        except Exception:
            logger.debug("could not render preview value for %s", fd.slug, exc_info=True)
            continue
        if text:
            parts.append({"slug": fd.slug, "text": text})
    return parts


def prime_node_summaries(root_node) -> None:
    """Snapshot pre-write summaries for ``root_node`` and all its descendants."""
    snapshot: dict[str, list[dict]] = {}
    stack = [root_node]
    while stack:
        node = stack.pop()
        snapshot[str(node.id)] = compute_node_summary_parts(node)
        stack.extend(node.children.all())
    _summary_snapshot.set(snapshot)


def summary_parts_for(node) -> list[dict]:
    """Pre-write parts for ``node`` if snapshotted, else its current parts.

    Nodes created during the patch have no snapshot entry, so they correctly
    fall through to their freshly written values.
    """
    snapshot = _summary_snapshot.get()
    if snapshot is not None:
        parts = snapshot.get(str(node.id))
        if parts is not None:
            return parts
    return compute_node_summary_parts(node)


def join_parts(parts, allowed_slugs=None) -> str:
    """Render stored parts, dropping any the viewer is not allowed to see."""
    texts = [
        p["text"] for p in (parts or [])
        if isinstance(p, dict) and p.get("text")
        and (allowed_slugs is None or p.get("slug") in allowed_slugs)
    ]
    return " · ".join(texts)
