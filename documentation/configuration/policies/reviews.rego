package udm.reviews

import rego.v1
import data.udm._proposal_ctx
import data.udm.is_moderator
import data.udm.current_status

# ─── Accept gate ────────────────────────────────────────────────────────────────
# Acceptance requires at least one requested reviewer, and every requested
# individual/group must have voted "accept".
# Uses set operations instead of `every` for regorus compatibility.

_reviews := object.get(input.entity.children, "reviews", [])
_reviewer_users := v if { v := input.entity.fields["requested-reviewer-users"].value; v != null } else := []
_reviewer_groups := v if { v := input.entity.fields["requested-reviewer-groups"].value; v != null } else := []

_accepting_user_ids := {r.fields.author.value.id |
	some r in _reviews
	r.fields.vote.value == "accept"
}

_accepting_group_ids := {rg.id |
	some rg in _reviewer_groups
	some member in rg.members
	some r in _reviews
	r.fields.author.value.id == member.id
	r.fields.vote.value == "accept"
}

all_reviews_accepted if {
	requested_user_ids := {u.id | some u in _reviewer_users}
	requested_group_ids := {g.id | some g in _reviewer_groups}

	count(requested_user_ids) + count(requested_group_ids) > 0

	print("[accept_gate] requested_users=", count(requested_user_ids),
	      "accepting_users=", count(_accepting_user_ids),
	      "missing_users=", count(requested_user_ids - _accepting_user_ids),
	      "requested_groups=", count(requested_group_ids),
	      "accepting_groups=", count(_accepting_group_ids),
	      "missing_groups=", count(requested_group_ids - _accepting_group_ids))

	# Every requested user must appear in the accepting set.
	count(requested_user_ids - _accepting_user_ids) == 0

	# Every requested group must have at least one accepting member.
	count(requested_group_ids - _accepting_group_ids) == 0

	print("[accept_gate] PASS all reviewers accepted")
}

# IDs of users who have cast any non-open vote (i.e., have actually reviewed).
_voted_user_ids := {r.fields.author.value.id |
	some r in _reviews
	r.fields.vote.value != null
	r.fields.vote.value != "open"
}

# IDs of requested groups for which at least one member has cast a non-open vote.
_voted_group_ids := {rg.id |
	some rg in _reviewer_groups
	some member in rg.members
	some r in _reviews
	r.fields.author.value.id == member.id
	r.fields.vote.value != null
	r.fields.vote.value != "open"
}


# ── Per-reviewer status breakdown ──

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some u in input.entity.fields["requested-reviewer-users"].value
	not u.id in _voted_user_ids
	msg := {
		"level": "warning",
		"text": sprintf("Pending review: %v has not yet voted.", [u.username]),
		"field_slug": "requested-reviewer-users",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some u in input.entity.fields["requested-reviewer-users"].value
	u.id in _voted_user_ids
	msg := {
		"level": "info",
		"text": sprintf("Review submitted: %v has voted.", [u.username]),
		"field_slug": "requested-reviewer-users",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some g in input.entity.fields["requested-reviewer-groups"].value
	not g.id in _voted_group_ids
	msg := {
		"level": "warning",
		"text": sprintf("Pending review: no member of '%v' has voted yet.", [g.name]),
		"field_slug": "requested-reviewer-groups",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some g in input.entity.fields["requested-reviewer-groups"].value
	g.id in _voted_group_ids
	msg := {
		"level": "info",
		"text": sprintf("Review submitted: a member of '%v' has voted.", [g.name]),
		"field_slug": "requested-reviewer-groups",
	}
}