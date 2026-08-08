"""Server-side rendering of markdown display fields (events-and-sync.md §1.4).

A `markdown_display` FormElement declares a Jinja template (type_config
"template") rendered against the policy's `effective` output plus `entity`,
`linked`, and `backlinks` — the same context shape templates get elsewhere
(mail templates, §1.6). Rego stays presentation-free; this is the one place
that turns `effective` into markdown text for the read-only display field.
"""
from __future__ import annotations

from userdefinedmodel.mailtemplates import get_environment, jsonify_context


def render_markdown_display(
    template_source: str,
    *,
    effective: dict,
    entity: dict,
    linked: dict,
    backlinks: dict,
) -> str:
    """Render one markdown_display element's template. Never raises — a
    template error yields an inline error marker so a broken template on one
    element cannot break the rest of the form."""
    if not template_source:
        return ""
    context = jsonify_context({
        "effective": effective,
        "entity": entity,
        "linked": linked,
        "backlinks": backlinks,
    })
    try:
        return get_environment(autoescape=False).from_string(template_source).render(**context)
    except Exception as exc:  # noqa: BLE001 - template authors are staff, not developers
        return f"*(template error: {exc})*"


def render_markdown_displays_for_entity(config_version, policy_output) -> dict[str, str]:
    """Render every markdown_display FormElement on config_version's form tree.

    Returns {slug: rendered_markdown}. `policy_output` is the
    PolicyEvaluationOutput of the VIEW evaluation that already resolved
    `effective`, `linked`, and `backlinks` (input_document carries the last
    two; §1.3/§2)."""
    from userdefinedmodel.models import FormElement

    elements = config_version.form_elements.filter(element_type=FormElement.ElementType.MARKDOWN_DISPLAY)
    if not elements:
        return {}

    input_doc = policy_output.input_document or {}
    entity = input_doc.get("entity") or {}
    linked = input_doc.get("linked") or {}
    backlinks = input_doc.get("backlinks") or {}

    return {
        el.slug: render_markdown_display(
            (el.type_config or {}).get("template", ""),
            effective=policy_output.effective,
            entity=entity,
            linked=linked,
            backlinks=backlinks,
        )
        for el in elements
    }
