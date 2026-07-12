package udm

import rego.v1

import data.udm.udmframeworkv1
import data.udm.udmframeworkv1.modules.sudo

# ─────────────────────────────────────────────────────────────────────────────
# Framework aggregator. The engine reads exactly ONE rule per evaluation:
#   data.udm.result       for entity actions (fixed schema, §3.1-1)
#   data.udm.type_result  for action == "public_type_fields"
# Modules under data.udm.udmframeworkv1.modules.* export:
#   allow, error_messages, success_messages,
#   viewable_fields / editable_fields   — sets of {"node": id, "field": slug},
#   valid_transitions                   — sets of {"node", "field", "name"},
#   actions, dashboard_columns, additional_result (object),
#   protected_fields (INTERNAL convention only — never part of result),
#   public_type_fields / TYPE_DESCRIPTION (type_result only).
# Schema-specific validators register under
#   data.udm.udmframeworkv1.validators[<schema_id>] and are dispatched against
#   every node of that schema in the tree (§3.3-12).
# ─────────────────────────────────────────────────────────────────────────────

default allow := false

default deny := false

# ─── allow / deny ─────────────────────────────────────────────────────────────

allow if {
	not deny
	m := udmframeworkv1.modules[name].allow
	print("[udm:allow] Module ", name, " allows this action: ", m)
	m
}

allow if {
	sudo.allow
	print("[udm:allow] Allowing due to sudo.")
}

deny if {
	any_critical_error
	print("[udm:deny] Denying due to critical error.")
}

deny if {
	any_error
	input.action == "transition"
	print("[udm:deny] Denying due to error and action is transition.")
}

# ─── Field grants (per node — whole tree, one pass) ───────────────────────────

editable_fields contains grant if {
	some grant in udmframeworkv1.modules[name].editable_fields
	print("[udm:editable] ", grant, " due to module: ", name)
}

viewable_fields contains grant if {
	some grant in udmframeworkv1.modules[name].viewable_fields
	print("[udm:viewable] ", grant, " due to module: ", name)
}

# {node_id: [slugs]} maps for the result object.
viewable_fields_map[node_id] := slugs if {
	some node_id in {g.node | some g in viewable_fields}
	slugs := sort([g.field | some g in viewable_fields; g.node == node_id])
}

editable_fields_map[node_id] := slugs if {
	some node_id in {g.node | some g in editable_fields}
	slugs := sort([g.field | some g in editable_fields; g.node == node_id])
}

# Protected fields are a STATIC config constant (config.PROTECTED_FIELDS):
# modules cannot read aggregated data.udm rules, because the dynamic
# modules[name] scan cannot recurse back into data.udm.

# ─── Messages ─────────────────────────────────────────────────────────────────

success_messages contains msg if {
	some msg in udmframeworkv1.modules[name].success_messages
	print("[udm:success message] ", msg, " from module: ", name)
}

error_messages contains msg if {
	some msg in udmframeworkv1.modules[name].error_messages
	print("[udm:error message] ", msg, " from module: ", name)
}

# Schema-specific validators (§3.3-12): regorus cannot call functions through
# a dynamic data reference, so validators are ordinary modules that iterate
# data.udm.tree_nodes_with_path themselves and gate on node.schema_id — their
# error_messages flow in through the module aggregation above. See
# _template.rego for the pattern.

any_critical_error if {
	some m in error_messages
	m.level == "critical"
	print("[udm:critical error] ", m)
}

any_error if {
	some m in error_messages
	m.level in {"critical", "error"}
	print("[udm:generic error] ", m)
}

messages contains msg if some msg in error_messages

messages contains msg if some msg in success_messages

# ─── Transitions / actions / dashboard ────────────────────────────────────────

valid_transitions contains t if {
	some t in udmframeworkv1.modules[name].valid_transitions
	print("[udm:valid transition] ", t, " due to module: ", name)
}

actions contains a if {
	some a in udmframeworkv1.modules[name].actions
}

# ─── Submodel operation grants (§6) ───────────────────────────────────────────

deletable_nodes contains n if {
	some n in udmframeworkv1.modules[name].deletable_nodes
	print("[udm:deletable] ", n, " due to module: ", name)
}

# Module export: {"node": parent_id, "field": slug, "viewable": [...], "editable": [...]}
# A grant's PRESENCE allows creating an item in that list; the lists are the
# field grants for the not-yet-saved item form. Grants from multiple modules
# for the same (parent, field) merge by union.
creatable_submodels contains g if {
	some g in udmframeworkv1.modules[name].creatable_submodels
	print("[udm:creatable] ", g, " due to module: ", name)
}

_creatable_entry(node_id, slug) := {
	"viewable": sort({v | some g in creatable_submodels; g.node == node_id; g.field == slug; some v in g.viewable}),
	"editable": sort({e | some g in creatable_submodels; g.node == node_id; g.field == slug; some e in g.editable}),
}

creatable_submodels_map[node_id] := fields_map if {
	some node_id in {g.node | some g in creatable_submodels}
	fields_map := {slug: _creatable_entry(node_id, slug) |
		some slug in {g.field | some g in creatable_submodels; g.node == node_id}
	}
}

# ─── Submodel op enforcement (§6.2) — not only cosmetics ─────────────────────
# The VIEW pre-check carried these grants over (additional_result); ops in the
# submitted payload that lack a grant become critical errors.

_submodel_ops[slug] := ops if {
	input.action in {"save", "preview"}
	some slug, entry in input.changed_fields
	is_array(entry.value)
	ops := [op | some op in entry.value; is_object(op); op.op]
	count(ops) > 0
}

error_messages contains msg if {
	some slug, ops in _submodel_ops
	some op in ops
	op.op == "create"
	not slug in object.keys(object.get(object.get(input.additional_result, "creatable_submodels", {}), input.entity.id, {}))
	msg := {
		"level": "critical",
		"text": sprintf("You may not add items to '%v'.", [slug]),
		"field_slug": slug,
	}
}

error_messages contains msg if {
	some slug, ops in _submodel_ops
	some op in ops
	op.op == "delete"
	not op.id in object.get(input.additional_result, "deletable_nodes", [])
	msg := {
		"level": "critical",
		"text": sprintf("You may not delete this item from '%v'.", [slug]),
		"field_slug": slug,
	}
}

error_messages contains msg if {
	some slug, ops in _submodel_ops
	some op in ops
	op.op == "update"
	some f, _ in object.get(op, "fields", {})
	not {"node": op.id, "field": f} in object.get(input.additional_result, "editable", [])
	msg := {
		"level": "critical",
		"text": sprintf("You may not change '%v' on this '%v' item.", [f, slug]),
		"field_slug": slug,
	}
}

error_messages contains msg if {
	some slug, ops in _submodel_ops
	some op in ops
	op.op == "create"
	grants := object.get(object.get(input.additional_result, "creatable_submodels", {}), input.entity.id, {})
	slug in object.keys(grants)
	some f, _ in object.get(op, "fields", {})
	not f in object.get(grants[slug], "editable", [])
	msg := {
		"level": "critical",
		"text": sprintf("Field '%v' may not be set when creating a '%v' item.", [f, slug]),
		"field_slug": slug,
	}
}

dashboard_columns contains c if {
	some c in udmframeworkv1.modules[name].dashboard_columns
}

# ─── additional_result (VIEW pre-check carry-over) ────────────────────────────
# Framework-provided: whether view was allowed and the per-node editable grant
# of THIS evaluation; passed back as input.additional_result to the subsequent
# save/transition/preview pass. Modules may add keys via their own
# additional_result objects (keys must not collide).

additional_result["view_allowed"] := allow

additional_result["editable"] := [g | some g in editable_fields]

additional_result["deletable_nodes"] := [n | some n in deletable_nodes]

additional_result["creatable_submodels"] := creatable_submodels_map

additional_result[k] := v if {
	some name
	some k, v in udmframeworkv1.modules[name].additional_result
}

# ─── Aggregate results (the ONLY rules the engine reads) ─────────────────────

result := {
	"allow": allow,
	"messages": [m | some m in messages],
	"viewable_fields": viewable_fields_map,
	"editable_fields": editable_fields_map,
	"valid_transitions": [t | some t in valid_transitions],
	"deletable_nodes": [n | some n in deletable_nodes],
	"creatable_submodels": creatable_submodels_map,
	"actions": [a | some a in actions],
	"dashboard_columns": [c | some c in dashboard_columns],
	"additional_result": additional_result,
}

_public_type_fields[k] := v if {
	some name
	some k, v in udmframeworkv1.modules[name].public_type_fields
}

_type_description[lang] := text if {
	some name
	some lang, text in udmframeworkv1.modules[name].TYPE_DESCRIPTION
}

type_result := {
	"public_type_fields": _public_type_fields,
	"type_description": _type_description,
}
