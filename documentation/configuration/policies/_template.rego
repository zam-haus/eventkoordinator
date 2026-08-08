# ─────────────────────────────────────────────────────────────────────────────
# Module contract (see documentation/rego-engine-review.md §3.3-10)
#
# Every instance policy is a *module* under data.udm.udmframeworkv1.modules.<name>.
# The framework aggregator (udm.rego) reads ONLY the exported names documented
# below and assembles data.udm.result / data.udm.type_result — the only rules
# the engine evaluates. Anything else a module defines is private to it.
#
# Composition semantics (across all modules and all policy files):
#   - `allow` is OR-ed: one module allowing is enough, unless udm.rego denies
#     (critical errors deny everything; error-level messages deny transitions).
#   - all sets (messages, grants, transitions, actions) are unioned.
#   - there is no ordering between modules; rules must not depend on it.
#
# HARD RULES (regorus):
#   - Modules must NEVER reference data.udm.* — the aggregator's dynamic
#     modules[name] scan cannot recurse back into data.udm (cycle). Shared
#     helpers live in data.udmtree (framework.rego) and static config in
#     modules/config.rego (e.g. config.PROTECTED_FIELDS).
#   - Cross-module FUNCTION calls do not resolve (e.g. roles.group_doc(x));
#     define helper functions locally in each module that needs them.
#
# Copy this file, rename the package, delete what you do not need.
# ─────────────────────────────────────────────────────────────────────────────
package udm.udmframeworkv1.modules.example

import data.udmtree
import rego.v1

# ── allow ────────────────────────────────────────────────────────────────────
# Boolean. Grants the current input.action. Deny-by-default.
default allow := false

allow if {
	input.action == "view"
	input.user.is_staff
}

# ── error_messages / success_messages ────────────────────────────────────────
# Sets of message objects:
#   {
#     "level":      "critical" | "error" | "warning" | "info" | "debug",
#     "text":       "human-readable string",
#     "field_slug": "<slug>" | "<slug>.<child_slug>" | null,
#   }
# The engine rewrites field_slug into highlight_fields: [field_slug]; dotted
# paths address submodel fields ("reviews.vote"). Gate save-blocking rules on
# input.action in {"save", "preview"} so the validation preview shows them.
error_messages contains msg if {
	input.action in {"save", "preview"}
	input.entity.fields.title.value == null
	msg := {
		"level": "error",
		"text": "A title is required.",
		"field_slug": "title",
	}
}

success_messages contains msg if {
	input.action in {"view", "preview"}
	msg := {
		"level": "info",
		"text": "Welcome.",
		"field_slug": null,
	}
}

# ── viewable_fields / editable_fields (per node — whole tree, one pass) ─────
# Sets of {"node": node_id, "field": slug} objects. The aggregator groups
# them into result.viewable_fields / result.editable_fields as
# {node_id: [slugs]} covering the ENTIRE model tree in this single
# evaluation — the engine never re-evaluates per node. Deny-by-default: a
# (node, field) pair nobody lists is not visible/editable; there is no
# "unrestricted" sentinel and no null.
#
# Iterate the tree via data.udmtree.tree_nodes; match schema-specific rules
# on node.schema_id (never on tree position).
viewable_fields contains {"node": node.id, "field": f} if {
	some node in udmtree.tree_nodes
	some f, _ in node.fields
	input.schemas[node.schema_id].properties.public == true
}

editable_fields contains {"node": input.entity.id, "field": "title"} if {
	input.user.id == input.entity.fields.owner.value
}

# NOTE: protected_fields is NOT part of the engine contract. It is a STATIC
# constant (config.PROTECTED_FIELDS in modules/config.rego) that the default
# grant modules (view.rego, save.rego) subtract; owning modules re-grant.

# ── valid_transitions (preview + shared authorization predicates) ───────────
# Set of {"node": node_id, "field": workflow_field_slug, "name": transition_name}.
# Iterate input.candidate_transitions (action == "preview") — match on the
# descriptor's properties/to_state, not hard-coded names, so new workflow
# transitions are covered without a policy edit.
valid_transitions contains {"node": node_id, "field": field_slug, "name": name} if {
	some node_id, wf_fields in input.candidate_transitions
	some field_slug, wf in wf_fields
	some name, descriptor in wf.transitions
	_transition_permitted(node_id, field_slug, name, descriptor)
}

# The SAME predicate authorizes real execution (action == "transition"),
# so preview and authorization cannot diverge.
allow if {
	input.action == "transition"
	_transition_permitted(input.node_id, input.field, input.transition, input.transition_descriptor)
}

_transition_permitted(_, _, _, descriptor) if {
	descriptor.properties.moderator_only
	"moderators" in {g.name | some g in input.user.groups}
}

# ── additional_result (carry-over from the VIEW pre-check pass) ──────────────
# The FRAMEWORK already provides {"view_allowed": <bool>, "editable": [grants]}
# — computed from this evaluation's allow / editable_fields — and the engine
# hands the VIEW pass's object back as input.additional_result to the
# save/transition/preview pass. Modules may add EXTRA keys (must not collide
# with view_allowed/editable or other modules' keys):
additional_result["example_status_seen"] := input.entity.fields.status.value

# ...and a later pass consumes the framework-provided carry-over:
error_messages contains msg if {
	input.action in {"save", "preview"}
	some slug, _ in input.changed_fields
	not {"node": input.entity.id, "field": slug} in input.additional_result.editable
	msg := {
		"level": "critical",
		"text": sprintf("Field %v was not editable before this save.", [slug]),
		"field_slug": slug,
	}
}

# ── linked_inputs / backlink_inputs (§2.2 dynamic link requests) ────────────
# Sets, unioned across ALL policy files, read in a REQUEST PHASE before the
# main evaluation — not part of data.udm.result. Requests may be conditional
# on input (type, workflow state, field values).
#
# linked_inputs contains "origin"                # forward path: follow the
#                                                 # entity_select field "origin"
# linked_inputs contains "origin.owner" if {      # deeper path: origin, then
#     input.entity.workflow_state == "published"  # ITS "owner" field
# }
#
# backlink_inputs contains {
# 	"name": "events",          # key under input.backlinks
# 	"source_type": "<event type id>",  # referencing UDMType id (UUID string)
# 	"source_field": "origin",  # entity_select slug on that type
# }
#
# Resolved paths land in input.linked (NodeDocument | null, or a LIST for a
# path that traverses an entity_select_multi segment); resolved backlinks
# land in input.backlinks.<name> as a list of NodeDocuments. Broken/unset
# references resolve to null — handle it, never assume presence.

# ── deletable_nodes / creatable_submodels (§6 submodel operations) ───────────
# deletable_nodes: set of child node ids the user may delete (list buttons).
# creatable_submodels: set of {"node": parent_id, "field": slug,
#   "viewable": [...], "editable": [...]} — a grant's PRESENCE allows creating
#   an item in that list; the lists drive the not-yet-saved item form. The
#   prospective child schema id is input.schemas[parent.schema_id]
#   .fields[slug].submodel_schema_id. The framework turns unauthorized
#   create/update/delete ops in changed_fields into critical errors using the
#   additional_result carry-over.
# deletable_nodes contains child.id if { ... }
# creatable_submodels contains {"node": input.entity.id, "field": "reviews",
# 	"viewable": ["vote"], "editable": ["vote"]} if { ... }

# ── actions (side effects, dispatched by the engine on save/transition) ─────
# Set of action objects; "type" selects the registered Python handler
# (actions.py registry). Each object is validated against the handler's
# schema at dispatch time.
# actions contains {
# 	"type": "send_notification",
# 	"recipient": input.entity.fields.owner.value,
# 	"template": "submitted",
# } if {
# 	input.action == "transition"
# 	input.transition_descriptor.to_state == "submitted"
# }

# ── dashboard_columns ────────────────────────────────────────────────────────
# Set of column descriptor objects for the dashboard endpoint.
# dashboard_columns contains {"slug": "title", "label": {"en": "Title"}}

# ── public_type_fields / TYPE_DESCRIPTION (type_result only) ─────────────────
# Read only for action == "public_type_fields" via data.udm.type_result.
# TYPE_DESCRIPTION := {"en": "## About this type…"}


# ─────────────────────────────────────────────────────────────────────────────
# Schema-specific validators (review §3.3-12)
#
# A validator is an ORDINARY module (regorus cannot dispatch functions through
# a dynamic registry ref) that iterates data.udmtree.tree_nodes_with_path and
# gates on node.schema_id. Written once against its schema, it fires for every
# node of that schema anywhere in the tree; `path` is the dotted prefix for
# highlight_fields ("" for the root, "reviews" for a child).
# ─────────────────────────────────────────────────────────────────────────────
# package udm.udmframeworkv1.modules.review_validator
#
# import data.udmtree
# import rego.v1
#
# REVIEW_SCHEMA_ID := "00000000-0000-0000-0000-000000000000"
#
# error_messages contains msg if {
# 	some [path, node] in udmtree.tree_nodes_with_path
# 	node.schema_id == REVIEW_SCHEMA_ID
# 	node.fields.vote.value == null
# 	msg := {
# 		"level": "error",
# 		"text": "A vote is required.",
# 		"field_slug": concat(".", [p | some p in [path, "vote"]; p != ""]),
# 	}
# }
