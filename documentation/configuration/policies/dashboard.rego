package udm

import rego.v1

# ─── dashboard_columns ────────────────────────────────────────────────────────
#
# Add this policy to any UDMType to inject computed columns into the UDM Dashboard.
# Each rule contributes one column object via the partial-set syntax so multiple
# dashboard.rego rules (or multiple policies attached to the same type) merge
# cleanly without conflict.
#
# Supported renderers:
#   "text"         – plain string value
#   "progress_bar" – value: {"current": N, "max": M, "color": "#hex"}
#   "meter"        – value: [{"label": "...", "value": N, "color": "#hex"}, ...]
#
# NOTE: Attach this policy to a UDMType via the UDM Admin → Rego Policies screen.
#       The file alone has no effect until it is stored as a Policy row and linked.

# ── Review progress bar ────────────────────────────────────────────────────────
# Shows how many requested reviewers have voted "accept" out of the total.

dashboard_columns contains col if {
	input.action == "view"
	reviews := object.get(input.entity.children, "reviews", [])
	total := count(reviews)
	total > 0
	accepted := count([r | some r in reviews; r.fields.vote.value == "accept"])
	col := {
		"key":      "review_progress",
		"label":    "Review Progress",
		"renderer": "progress_bar",
		"value": {
			"current": accepted,
			"max":     total,
			"color":   "#22c55e",
		},
	}
}

# ── Review breakdown meter ─────────────────────────────────────────────────────
# Stacked bar showing the distribution of review votes.

dashboard_columns contains col if {
	input.action == "view"
	reviews := object.get(input.entity.children, "reviews", [])
	count(reviews) > 0
	accepted := count([r | some r in reviews; r.fields.vote.value == "accept"])
	revise   := count([r | some r in reviews; r.fields.vote.value == "revise"])
	rejected := count([r | some r in reviews; r.fields.vote.value == "reject"])
	pending  := count([r | some r in reviews
		v := object.get(r.fields, "vote", {})
		val := object.get(v, "value", null)
		val == null
	]) + count([r | some r in reviews; r.fields.vote.value == "open"])
	col := {
		"key":      "review_breakdown",
		"label":    "Reviews",
		"renderer": "meter",
		"value": [
			{"label": "Accept",  "value": accepted, "color": "#22c55e"},
			{"label": "Revise",  "value": revise,   "color": "#f59e0b"},
			{"label": "Reject",  "value": rejected,  "color": "#ef4444"},
			{"label": "Pending", "value": pending,   "color": "#9ca3af"},
		],
	}
}

# ── Submission checklist progress bar ─────────────────────────────────────────
# Generic example: shows how many non-null top-level fields are filled.
# Replace the field list with slugs relevant to the type this policy is attached to.

dashboard_columns contains col if {
	input.action == "view"
	tracked_fields := ["title", "abstract", "description"]
	filled := count([f | some f in tracked_fields; input.entity.fields[f].value != null])
	col := {
		"key":      "completeness",
		"label":    "Completeness",
		"renderer": "progress_bar",
		"value": {
			"current": filled,
			"max":     count(tracked_fields),
			"color":   "#3b82f6",
		},
	}
}
