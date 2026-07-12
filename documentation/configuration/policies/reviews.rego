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

# Local group lookup: regorus cannot resolve cross-module function calls,
# so each module defines its own (input.groups is keyed by stringified PK).
_group_doc(gid) := input.groups[sprintf("%v", [gid])]


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

_accepting_user_ids := {r.fields.author.value |
	some r in _reviews
	r.fields.vote.value == "accept"
}

_accepting_group_ids := {rg |
	some rg in _reviewer_groups
	some member_id in _group_doc(rg).member_ids
	some r in _reviews
	r.fields.author.value == member_id
	r.fields.vote.value == "accept"
}

all_reviews_accepted if {
	requested_user_ids := {u | some u in _reviewer_users}
	requested_group_ids := {g | some g in _reviewer_groups}

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
_voted_user_ids := {r.fields.author.value |
	some r in _reviews
	r.fields.vote.value != null
	r.fields.vote.value != "open"
}

# IDs of requested groups for which at least one member has cast a non-open vote.
_voted_group_ids := {rg |
	some rg in _reviewer_groups
	some member_id in _group_doc(rg).member_ids
	some r in _reviews
	r.fields.author.value == member_id
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
	some u in _reviewer_users
	not u in _voted_user_ids
	msg := {
		"level": "warning",
		"text": sprintf("Pending review: %v has not yet voted.", [input.users[u].username]),
		"field_slug": "requested-reviewer-users",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some u in _reviewer_users
	u in _voted_user_ids
	msg := {
		"level": "info",
		"text": sprintf("Review submitted: %v has voted.", [input.users[u].username]),
		"field_slug": "requested-reviewer-users",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some g in _reviewer_groups
	not g in _voted_group_ids
	msg := {
		"level": "warning",
		"text": sprintf("Pending review: no member of '%v' has voted yet.", [_group_doc(g).name]),
		"field_slug": "requested-reviewer-groups",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some g in _reviewer_groups
	g in _voted_group_ids
	msg := {
		"level": "info",
		"text": sprintf("Review submitted: a member of '%v' has voted.", [_group_doc(g).name]),
		"field_slug": "requested-reviewer-groups",
	}
}

# ── tab-submission: reviewer assignment ────────────────────────────────────────
# The source message in proposals uses field_slug: null, so it cannot be relayed
# through _field_to_tabs.
error_messages contains msg if {
	input.action in {"save", "preview"}
	_can_view
	not is_moderator
	_changing_reviewer_assignments
	msg := {"level": "critical", "text": "Only moderators may change reviewer assignments.", "field_slug": "tab-submission"}
}

error_messages contains msg if {
	input.action in {"save", "preview"}
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
# node_id identifies the specific review node; we verify the review's author
# matches the current user. Moderators not in the reviewer lists cannot vote.
# ONE predicate serves both the real transition and the preview matrix.
_vote_permitted(node_id, field_slug, name) if {
	field_slug == "vote"
	name in {"accept", "reject", "revise", "reset"}
	current_status == "submitted"
	some r in _reviews
	r.id == node_id
	r.fields.author.value == input.user.id
}

allow if {
	input.action == "transition"
	_vote_permitted(input.node_id, input.field, input.transition)
	print(
		"[allow:transition] vote user=", input.user.username,
		"transition=", input.transition, "node=", input.node_id,
	)
}

valid_transitions contains {"node": node_id, "field": field_slug, "name": name} if {
	some node_id, wf_fields in input.candidate_transitions
	some field_slug, wf in wf_fields
	some name, _ in wf.transitions
	_vote_permitted(node_id, field_slug, name)
}

protected_fields := ["reviews", "requested-reviewer-users", "requested-reviewer-groups"]

# Grants are per-node objects (contract §3.1-1). "reviews" on the root also
# exposes/edits the review child nodes, so the same conditions grant every
# field of each review node.

_root := input.entity.id

# Moderators can manage the reviewer assignment fields.
editable_fields contains {"node": _root, "field": "requested-reviewer-groups"} if roles.is_moderator

editable_fields contains {"node": _root, "field": "requested-reviewer-users"} if roles.is_moderator

# Reviewers can add/update their own review submodel while the proposal is submitted.
# The author field within a review is set automatically and blocked from editing above.
_reviews_editable if {
	roles.is_reviewer
	current_status == "submitted"
	print("[reviews:editable_fields] user=", input.user.username, "is reviewer and proposal is submitted")
}

editable_fields contains {"node": _root, "field": "reviews"} if _reviews_editable

editable_fields contains {"node": r.id, "field": f} if {
	_reviews_editable
	some r in _reviews
	r.fields.author.value == input.user.id
	some f, _ in r.fields
}

# Moderators always see reviewer fields, even in draft or when also owner/editor.
_reviews_visible if roles.is_moderator

_reviews_visible if {
	roles.is_owner_or_editor
	workflow.is_status_post_review
}

viewable_fields contains {"node": _root, "field": "reviews"} if {
	_reviews_visible
	input.entity.fields.reviews
}

viewable_fields contains {"node": r.id, "field": f} if {
	_reviews_visible
	some r in _reviews
	some f, _ in r.fields
}

# Reviewers see the reviews list and their own review node while voting.
viewable_fields contains {"node": _root, "field": "reviews"} if _reviews_editable

viewable_fields contains {"node": r.id, "field": f} if {
	_reviews_editable
	some r in _reviews
	r.fields.author.value == input.user.id
	some f, _ in r.fields
}

viewable_fields contains {"node": _root, "field": "requested-reviewer-groups"} if {
	roles.is_moderator
	input.entity.fields["requested-reviewer-groups"]
}

viewable_fields contains {"node": _root, "field": "requested-reviewer-users"} if {
	roles.is_moderator
	input.entity.fields["requested-reviewer-users"]
}
