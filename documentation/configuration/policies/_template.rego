# ─────────────────────────────────────────────────────────────────────────────
# Module contract (draft — see documentation/rego-engine-review.md §3.3-10)
#
# Every instance policy is a *module* under data.udm.udmframeworkv1.modules.<name>.
# The framework aggregator (udm.rego) reads ONLY the exported names documented
# below; anything else a module defines is private to it and silently ignored.
#
# Composition semantics (across all modules and all policy files):
#   - `allow` is OR-ed: one module allowing is enough, unless udm.rego denies.
#   - all sets (messages, fields, transitions, actions) are unioned.
#   - there is no ordering between modules; rules must not depend on it.
#
# Copy this file, rename the package, delete what you do not need.
# ─────────────────────────────────────────────────────────────────────────────
package udm.udmframeworkv1.modules.example

import data.udm.udmframeworkv1.input_schema
import rego.v1

# ── allow ────────────────────────────────────────────────────────────────────
# Boolean. Grants the current input.action. Deny-by-default: if no module
# allows, the request is refused. udm.rego may still override with deny
# (critical errors always deny; error-level messages deny transitions).
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
# Semantics enforced by udm.rego / the engine:
#   - any critical error  => deny (every action)
#   - any error/critical  => deny transitions
#   - the engine rewrites field_slug into highlight_fields: [field_slug];
#     dotted paths address submodel fields ("reviews.vote").
error_messages contains msg if {
	input.action == "save"
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
# Iterate the tree via the framework's node walker; match schema-specific
# rules on node.schema_id (never on tree position).
viewable_fields contains {"node": node.id, "field": slug} if {
	some node in input_schema.tree_nodes
	some slug, _ in node.fields
	input.schemas[node.schema_id].properties.public == true
}

editable_fields contains {"node": input.entity.id, "field": "title"} if {
	input.user.id == input.entity.fields.owner.value
}

# NOTE: protected_fields is deliberately NOT part of the engine contract.
# It remains an internal convention: modules may export it and the framework
# default-grant modules (save.rego, view.rego) subtract it before granting.

# ── additional_result (carry-over from the VIEW pre-check pass) ──────────────
# Object (key => value, unioned across modules). Whatever the VIEW pass puts
# here is handed back verbatim as input.additional_result to the subsequent
# save/transition/preview evaluation on the patched state. Use it to record
# facts about the PERSISTED state that the later pass must compare against
# (replaces the fixed view_was_allowed / old_editable_fields input keys).
additional_result["was_allowed"] := allow

additional_result["editable"] := editable_fields

# ...and the save pass consumes it:
error_messages contains msg if {
	input.action == "save"
	some slug, _ in input.changed_fields
	not {"node": input.entity.id, "field": slug} in input.additional_result.editable
	msg := {
		"level": "critical",
		"text": sprintf("Field %v was not editable before this save.", [slug]),
		"field_slug": slug,
	}
}

# ── valid_transitions (preview + shared authorization predicates) ───────────
# Set of {"node": node_id, "field": workflow_field_slug, "name": transition_name}.
# Only evaluated meaningfully when input.candidate_transitions is populated
# (action == "preview"). Iterate the candidate descriptors — match on
# descriptor properties / to_state, not hard-coded names, so new workflow
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


# ─────────────────────────────────────────────────────────────────────────────
# Schema-specific validators (review §3.3-12)
#
# Validators are registered per model-schema UUID, NOT per module. The
# framework walker visits every node in input.entity's tree (root included),
# looks up data.udm.udmframeworkv1.validators[node.schema_id], and unions the
# resulting error_messages. Write the validator once against its schema; it
# fires for every node of that schema anywhere in the tree — no node-type or
# tree-position branching.
#
# `node` is the node's own document ({id, schema_id, fields, children, ...});
# `path` is the dotted highlight prefix for this node ("" for the root,
# "reviews" for a child, etc.) — prepend it to field slugs in field_slug.
# ─────────────────────────────────────────────────────────────────────────────
# package udm.udmframeworkv1.validators["00000000-0000-0000-0000-000000000000"]
#
# error_messages(node, path) := {msg} if {
# 	node.fields.vote.value == null
# 	msg := {
# 		"level": "error",
# 		"text": "A vote is required.",
# 		"field_slug": concat(".", [p | some p in [path, "vote"]; p != ""]),
# 	}
# }
