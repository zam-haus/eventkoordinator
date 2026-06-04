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
# One slot per requested user/group; best vote wins for groups.
# Mirrors the accept-gate logic in proposals.rego so the meter is consistent
# with what is required for acceptance.

# Module-level null-safe accessors (if/else only valid at rule scope, not in a query body).
_dash_reviewer_users  := v if { v := input.entity.fields["requested-reviewer-users"].value;  v != null } else := []
_dash_reviewer_groups := v if { v := input.entity.fields["requested-reviewer-groups"].value; v != null } else := []

dashboard_columns contains col if {
	input.action == "view"
	reviews         := object.get(input.entity.children, "reviews", [])
	reviewer_users  := _dash_reviewer_users
	reviewer_groups := _dash_reviewer_groups

	total_slots := count(reviewer_users) + count(reviewer_groups)
	total_slots > 0

	# Sets of reviewer IDs that have cast each vote
	accepting_ids := {r.fields.author.value.id | some r in reviews; r.fields.vote.value == "accept"}
	revising_ids  := {r.fields.author.value.id | some r in reviews; r.fields.vote.value == "revise"}
	rejecting_ids := {r.fields.author.value.id | some r in reviews; r.fields.vote.value == "reject"}

	# User slots: direct match by ID
	u_accepted := count({u.id | some u in reviewer_users; u.id in accepting_ids})
	u_revised  := count({u.id | some u in reviewer_users; u.id in revising_ids})
	u_rejected := count({u.id | some u in reviewer_users; u.id in rejecting_ids})
	u_pending  := count(reviewer_users) - u_accepted - u_revised - u_rejected

	# Group slots: best-vote-wins using set differences to avoid negation in comprehensions
	all_group_ids   := {g.id | some g in reviewer_groups}
	g_accept_ids    := {g.id | some g in reviewer_groups; some m in g.members; m.id in accepting_ids}
	g_no_accept_ids := all_group_ids - g_accept_ids
	g_revise_ids    := {g.id | some g in reviewer_groups; g.id in g_no_accept_ids; some m in g.members; m.id in revising_ids}
	g_no_acrev_ids  := g_no_accept_ids - g_revise_ids
	g_reject_ids    := {g.id | some g in reviewer_groups; g.id in g_no_acrev_ids; some m in g.members; m.id in rejecting_ids}
	g_pending_cnt   := count(g_no_acrev_ids) - count(g_reject_ids)

	accepted := u_accepted + count(g_accept_ids)
	revised  := u_revised  + count(g_revise_ids)
	rejected := u_rejected + count(g_reject_ids)
	pending  := u_pending  + g_pending_cnt

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
