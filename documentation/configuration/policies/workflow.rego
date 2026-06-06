package udm.udmframeworkv1.modules.workflow

import rego.v1

# Human-readable status labels for info messages.
_STATUS_LABEL := {
	"draft": "Draft",
	"submitted": "Submitted — awaiting review",
	"revise": "Revision requested",
	"accepted": "Accepted",
	"rejected": "Rejected",
}

# States in which owner/editors may edit proposal content.
EDITABLE_STATUSES := {"draft", "revise"}

# States in which reviews become visible to the owner/editors.
# Reviews are hidden while the proposal is in draft or submitted,
# so owners cannot see reviewer feedback until a decision is reached.
POST_REVIEW_STATUSES := {"revise", "accepted", "rejected"}

POST_DRAFT_STATUSES := {"revise", "accepted", "rejected", "submitted"}

is_status_editable if {
	current_status
	EDITABLE_STATUSES[current_status]
	print("[workflow] is_status_editable status=", current_status)
} else := false

is_status_post_review if {
	current_status
	POST_REVIEW_STATUSES[current_status]
	print("[workflow] is_status_post_review status=", current_status)
} else := false

is_status_post_draft if {
	current_status
	POST_DRAFT_STATUSES[current_status]
	print("[workflow] is_status_post_draft status=", current_status)
} else := false

label_for(status) := label if {
	label := _STATUS_LABEL[status]
	label != null
}

# ─── Current workflow state ────────────────────────────────────────────────────
# Workflow fields serialize as the state name string (e.g. "draft").
current_status := current_status_val if {
	current_status_val := input.entity.fields.status.value
	current_status_val != null
}

current_status_label := label_for(current_status)
