package udm.udmframeworkv1.modules.config

import rego.v1

_deadline_human := "2026-12-31"
_deadline := "2026-12-31T23:59:59Z"

# ─── Configuration ─────────────────────────────────────────────────────────────

# Deadline after which new proposals may no longer be created (ISO-8601 UTC).
# Must match the deadline in description.rego.
SUBMISSION_DEADLINE := "2026-12-31T23:59:59Z"

# Group names whose members act as proposal moderators.
MODERATOR_GROUP_NAMES := ["moderators"]



# Root-entity field slugs excluded from the default view/save grants; the
# owning modules re-grant them explicitly (proposal_id.rego, reviews.rego).
# Static by design: modules must not read aggregated data.udm rules (the
# dynamic modules[name] scan cannot recurse back into data.udm).
PROTECTED_FIELDS := {
	"proposal-id", "owner", "editors",
	"reviews", "requested-reviewer-users", "requested-reviewer-groups",
}

# Structural layout field types: carry no entity data and are ALWAYS viewable
# (tabs, navigation, save buttons render for everyone; clickability is a
# separate, GUI-side decision driven by the save preview / editable grants).
STRUCTURAL_TYPES := {
	"tab_container", "tab", "save_button",
	"hstack", "hstack_group", "tab_prev", "tab_next",
}
