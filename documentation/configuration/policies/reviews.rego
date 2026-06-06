package udm.udmframeworkv1.modules.reviews

import data.udm.udmframeworkv1.modules.proposals._can_view
import data.udm.udmframeworkv1.modules.proposals._proposal_ctx
import data.udm.udmframeworkv1.modules.workflow.current_status
import data.udm.udmframeworkv1.modules.roles.is_moderator
import data.udm.udmframeworkv1.modules.roles.is_owner_or_editor
import data.udm.udmframeworkv1.modules.roles.is_reviewer
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.workflow
import rego.v1

# ─── Accept gate ────────────────────────────────────────────────────────────────
# Acceptance requires at least one requested reviewer, and every requested
# individual/group must have voted "accept".
# Uses set operations instead of `every` for regorus compatibility.

_reviews := object.get(input.entity.children, "reviews", [])

default _reviewer_users := []

_reviewer_users := v if {
	v := input.entity.fields["requested-reviewer-users"].value
	v != null
}

default _reviewer_groups := []

_reviewer_groups := v if {
	v := input.entity.fields["requested-reviewer-groups"].value
	v != null
}

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

	print(
		"[accept_gate] requested_users=", count(requested_user_ids),
		"accepting_users=", count(_accepting_user_ids),
		"missing_users=", count(requested_user_ids - _accepting_user_ids),
		"requested_groups=", count(requested_group_ids),
		"accepting_groups=", count(_accepting_group_ids),
		"missing_groups=", count(requested_group_ids - _accepting_group_ids),
	)

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

_changing_reviewer_assignments if input.changed_fields["requested-reviewer-groups"]
_changing_reviewer_assignments if input.changed_fields["requested-reviewer-users"]

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

# ── tab-submission: reviewer assignment ────────────────────────────────────────
# The source message in proposals uses field_slug: null, so it cannot be relayed
# through _field_to_tabs.
error_messages contains msg if {
	input.action == "save"
	_can_view
	not is_moderator
	_changing_reviewer_assignments
	msg := {"level": "critical", "text": "Only moderators may change reviewer assignments.", "field_slug": "tab-submission"}
}

error_messages contains msg if {
	input.action == "save"
	_can_view
	not is_moderator
	_changing_reviewer_assignments
	print(
		"[block:reviewer-assignment] user=", input.user.username,
		"is not a moderator, changed_fields=", input.changed_fields,
	)
	msg := {
		"level": "critical",
		"text": "Only moderators may change reviewer assignments.",
		"field_slug": null,
	}
}

# A reviewer may transition only the vote field on their own review node.
# input.node_id identifies the specific review node; we verify the review's author
# matches the current user. Moderators not in the reviewer lists cannot vote.
allow if {
	input.action == "transition"
	input.field == "vote"
	input.transition in {"accept", "reject", "revise", "reset"}
	current_status == "submitted"
	some r in _reviews
	r.id == input.node_id
	r.fields.author.value.id == input.user.id
	print(
		"[allow:transition] vote user=", input.user.username,
		"transition=", input.transition, "node=", input.node_id,
	)
}

protected_fields := ["reviews", "requested-reviewer-users", "requested-reviewer-groups"]

# Moderators can manage the reviewer assignment fields.
editable_fields contains "requested-reviewer-groups" if roles.is_moderator
editable_fields contains "requested-reviewer-users" if roles.is_moderator

# Reviewers can add/update their own review submodel while the proposal is submitted.
# The author field within a review is set automatically and blocked from editing above.
editable_fields contains "reviews" if {
	roles.is_reviewer
	current_status == "submitted"
	print("[reviews:editable_fields] user=", input.user.username, "is reviewer and proposal is submitted")
}

# Moderators always see reviewer fields, even in draft or when also owner/editor.
viewable_fields contains "reviews" if {
	roles.is_moderator
	input.entity.fields.reviews
}

viewable_fields contains "requested-reviewer-groups" if {
	roles.is_moderator
	input.entity.fields["requested-reviewer-groups"]
}

viewable_fields contains "requested-reviewer-users" if {
	roles.is_moderator
	input.entity.fields["requested-reviewer-users"]
}

viewable_fields contains "reviews" if {
	roles.is_owner_or_editor
	workflow.is_status_post_review
}
