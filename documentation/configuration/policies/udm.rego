package udm

import rego.v1

import data.udm.udmframeworkv1.transitions
import data.udm.udmframeworkv1.reviews
import data.udm.udmframeworkv1.validation_rules
import data.udm.udmframeworkv1.proposals


# ─── Imports from other policy modules ─────────────────────────────────────────────
allow if transitions.allow
allow if proposals.allow

success_messages contains msg if some msg in transitions.success_messages
error_messages contains msg if some msg in transitions.error_messages

success_messages contains msg if some msg in reviews.success_messages
error_messages contains msg if some msg in reviews.error_messages

success_messages contains msg if some msg in validation_rules.success_messages
error_messages contains msg if some msg in validation_rules.error_messages
