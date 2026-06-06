package udm

import rego.v1

import data.udm.udmframeworkv1
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.sudo

default allow := false
default deny := false

# ─── Imports from other policy modules ─────────────────────────────────────────────
allow if {
	not deny
	m:= udmframeworkv1.modules[name]
	print("Module ", name, " allows this action: ", m.allow)
	m.allow
}

allow if {
	sudo.allow
	print("Allowing due to sudo.")
}

deny if {
	any_critical_error
	print("Denying due to critical error.")
}

deny if {
	any_error
	input.action == "transition"
	print("Denying due to error and action is transition.")
}

debug if {
    print("allow: ", allow)
    print("deny: ", deny)
}

editable_fields contains field_slug if {
    m := udmframeworkv1.modules[name].editable_fields
    some field_slug in m
    print("Editable field: ", field_slug, " due to module: ", name)
}

viewable_fields contains field_slug if {
    m := udmframeworkv1.modules[name].viewable_fields
    some field_slug in m
    print("Viewable field: ", field_slug, " due to module: ", name)
}


# Protected fields are those that cannot be edited or viewed by default; they require explicit allow rules in the modules. This is useful for fields that are sensitive or require special permissions.
protected_fields contains field_slug if {
    m := udmframeworkv1.modules[name].protected_fields
    some field_slug in m
    print("Protected field: ", field_slug, " due to module: ", name)
}

success_messages contains msg if {
	some m in udmframeworkv1.modules
	some msg in m.success_messages
}

error_messages contains msg if {
	some m in udmframeworkv1.modules
	some msg in m.error_messages
}

any_critical_error if {
	some m in error_messages
	m.level == "critical"
	print(m)
}

any_error if {
	some m in error_messages
	m.level in {"critical", "error"}
	print(m)
}

# ─── messages (engine reads this: union of error and success messages) ──────────
messages contains msg if some msg in error_messages
messages contains msg if some msg in success_messages
