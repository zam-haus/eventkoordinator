package udm.udmframeworkv1.modules.event

import data.udmtree
import rego.v1

# ─────────────────────────────────────────────────────────────────────────────
# event.rego — demo UDM type for events-and-sync.md.
#
# Deliberately self-contained (does not reuse save.rego/view.rego, which are
# Proposal-specific) so it stands alone as a minimal worked example of:
#   §1.1  entity_select field ("origin") + the immutable-after-create pattern
#   §1.3  effective values, coalescing an override with the linked proposal
#   §2    a dynamic linked_inputs request ("origin")
#   §1.4  the effective object rendered into a markdown_display element
#         (type_config on the "summary" FormElement, see UDM_BUNDLE.json)
#   §1.5  Proposal's "linked_events" backlink_list element (UDM_BUNDLE.json)
#         reads this type back via its "origin" field
#   §3.2  the sync_status element (UDM_BUNDLE.json) — empty until a target
#         exists (sync_core has no ported plugin yet)
# Loaded alongside framework.rego (tree walker) + udm.rego (aggregator) +
# sudo.rego (bypass for superusers in sudo mode) — the same shared Policy
# rows Proposal uses.
# ─────────────────────────────────────────────────────────────────────────────

default allow := false

allow if {
	input.action in {"view", "browse"}
	input.user.is_active
}

allow if {
	input.action in {"save", "create", "delete", "preview"}
	input.user.is_staff
}

# ── field grants ────────────────────────────────────────────────────────────
# Every field is viewable. Every field is editable by staff EXCEPT "origin"
# once it carries a value — the immutable-after-create pattern (§1.1): no
# schema flag, just an editable_fields exclusion gated on the field's own
# current value.

viewable_fields contains {"node": node.id, "field": f} if {
	some node in udmtree.tree_nodes
	some f, _ in node.fields
}

_NO_VALUE_DISPLAY_TYPES := {"markdown_display", "backlink_list", "sync_status"}

editable_fields contains {"node": node.id, "field": f} if {
	input.user.is_staff
	some node in udmtree.tree_nodes
	some f, entry in node.fields
	f != "origin"
	not entry.data_type in _NO_VALUE_DISPLAY_TYPES
}

editable_fields contains {"node": node.id, "field": "origin"} if {
	input.user.is_staff
	some node in udmtree.tree_nodes
	node.fields.origin.value == null
}

# ── linked_inputs (§2) ───────────────────────────────────────────────────────
# Request the linked proposal document so `effective` (below) can read its
# title. Unconditional here; a real policy might gate this on workflow state.

linked_inputs contains "origin"

# ── effective (§1.3) ─────────────────────────────────────────────────────────
# title_override wins when set; otherwise fall back to the linked proposal's
# title. input.linked.origin is null when origin is unset OR the reference is
# dangling (deleted proposal) — both fall through to the same default.

effective["title"] := v if {
	v := input.entity.fields.title_override.value
	v != null
}

effective["title"] := input.linked.origin.fields.title.value if {
	input.entity.fields.title_override.value == null
	input.linked.origin != null
}

effective["title"] := "(untitled event)" if {
	input.entity.fields.title_override.value == null
	input.linked.origin == null
}
