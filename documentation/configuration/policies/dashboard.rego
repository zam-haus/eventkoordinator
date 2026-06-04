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

# ── Review completeness meter ──────────────────────────────────────────────────
# Groups reviews by their requested-reviewer-groups / requested-reviewer-users
# slot key, takes the worst vote per slot, and plots the distribution.

dashboard_columns contains col if {
	input.action == "view"
	reviews := object.get(input.entity.children, "reviews", [])
	count(reviews) > 0

	# Higher severity = worse outcome
	sev := {"accept": 0, "revise": 1, "open": 2, "reject": 3}

	# Stable string key per requested-reviewer slot
	slots := {k |
		some r in reviews
		g := object.get(object.get(r.fields, "requested-reviewer-groups", {}), "value", null)
		u := object.get(object.get(r.fields, "requested-reviewer-users",  {}), "value", null)
		k := sprintf("g:%v|u:%v", [g, u])
	}

	# Per slot: worst (max) severity across all votes cast for that slot
	slot_worst := {k: ws |
		some k in slots
		ws := max({s |
			some r in reviews
			g := object.get(object.get(r.fields, "requested-reviewer-groups", {}), "value", null)
			u := object.get(object.get(r.fields, "requested-reviewer-users",  {}), "value", null)
			sprintf("g:%v|u:%v", [g, u]) == k
			v := object.get(object.get(r.fields, "vote", {}), "value", null)
			s := object.get(sev, v, 2)
		})
	}

	accepted := count({k | some k in slots; slot_worst[k] == 0})
	revised  := count({k | some k in slots; slot_worst[k] == 1})
	pending  := count({k | some k in slots; slot_worst[k] == 2})
	rejected := count({k | some k in slots; slot_worst[k] == 3})

	col := {
		"key":      "completeness",
		"label":    "Review Completeness",
		"renderer": "meter",
		"value": [
			{"label": "Accept",  "value": accepted, "color": "#22c55e"},
			{"label": "Revise",  "value": revised,  "color": "#f59e0b"},
			{"label": "Pending", "value": pending,  "color": "#9ca3af"},
			{"label": "Reject",  "value": rejected, "color": "#ef4444"},
		],
	}
}
