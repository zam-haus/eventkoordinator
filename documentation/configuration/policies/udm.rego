package udm

import rego.v1

import data.udm.udmframeworkv1

any_modules if {
	udmframeworkv1.modules
	print("Loaded modules: ", object.keys(udmframeworkv1.modules))
}

# ─── Imports from other policy modules ─────────────────────────────────────────────
allow if {
	any_modules
	some m in udmframeworkv1.modules
	m.allow
}

success_messages contains msg if {
	any_modules
	some m in udmframeworkv1.modules
	some msg in m.success_messages
}

error_messages contains msg if {
	any_modules
	some m in udmframeworkv1.modules
	some msg in m.error_messages
}

# ─── messages (engine reads this: union of error and success messages) ──────────
messages contains msg if some msg in error_messages
messages contains msg if some msg in success_messages
