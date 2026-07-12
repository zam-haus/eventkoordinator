package udm.udmframeworkv1.modules.tabs

import data.udm.udmframeworkv1.modules.validation_rules
import rego.v1

# Maps each form field slug to the tab(s) that contain it.
_field_to_tabs := {
	"abstract": ["tab-general"],
	"description": ["tab-general"],
	"language": ["tab-general"],
	"submission-type": ["tab-general"],
	"area": ["tab-general"],
	"photo": ["tab-general"],
	"photo-copyright-consent": ["tab-general"],
	"max-participants": ["tab-participants"],
	"duration-days": ["tab-scheduling"],
	"occurrence-count": ["tab-scheduling"],
	"preferred-dates": ["tab-scheduling"],
	"speakers": ["tab-submission"],
	"requested-reviewer-groups": ["tab-submission"],
	"requested-reviewer-users": ["tab-submission"],
	"reviews": ["tab-submission"],
}

# ─── Tab-level message propagation ─────────────────────────────────────────────
# For each critical/error/warning from validation_rules, emit a corresponding
# message on every tab that contains the affected field, so the UI can display
# a severity icon on the tab button.

error_messages contains msg if {
	some src in validation_rules.error_messages
	src.level in {"critical", "error", "warning"}
	some tab in _field_to_tabs[src.field_slug]
	msg := {"level": src.level, "text": src.text, "field_slug": tab}
}
