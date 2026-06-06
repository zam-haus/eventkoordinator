package udm_test

import rego.v1

import data.udm

# ─── User fixtures ─────────────────────────────────────────────────────────────
# Minimal objects — only fields referenced by the policies are required.

# Owner of BASE_DOCUMENT's entity; also a moderator and superuser.
USER_OWNER := {
	"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
	"username": "admin",
	"email": "redacted@example.com",
	"phone_number": "",
	"is_active": true,
	"is_staff": true,
	"is_superuser": true,
	"groups": [
		{"id": 1, "name": "Authenticated Users"},
		{"id": 2, "name": "moderators"},
	],
	"permissions": [],
}

# Regular user; direct reviewer and group-reviewer in BASE_DOCUMENT (id in
# requested-reviewer-users; group 1 in requested-reviewer-groups).
USER_PHI1010 := {
	"id": "d2d9d14a-71ff-4055-8805-3f3cc74c267f",
	"username": "phi1010",
	"email": "redacted@example.com",
	"phone_number": "",
	"is_active": true,
	"is_staff": false,
	"is_superuser": false,
	"groups": [{"id": 1, "name": "Authenticated Users"}],
	"permissions": [],
}

# Standalone moderator; not the entity owner. Also a group-reviewer because
# the moderators group (id 2) appears in requested-reviewer-groups.
USER_MODERATOR := {
	"id": "aaaaaaaa-0000-0000-0000-000000000001",
	"username": "moderator1",
	"email": "redacted@example.com",
	"phone_number": "",
	"is_active": true,
	"is_staff": false,
	"is_superuser": false,
	"groups": [{"id": 2, "name": "moderators"}],
	"permissions": [],
}

# Unrelated user with no access to this entity.
USER_NONE := {
	"id": "bbbbbbbb-0000-0000-0000-000000000001",
	"username": "stranger",
	"email": "redacted@example.com",
	"phone_number": "",
	"is_active": true,
	"is_staff": false,
	"is_superuser": false,
	"groups": [],
	"permissions": [],
}

# ─── Submodel fixtures ─────────────────────────────────────────────────────────

SPEAKER_WITH_BIO := {
	"id": "ef54157c-8f05-469d-91c2-873109212a11",
	"type": "submodel:speakers",
	"config_version_id": "f7951aee-3a82-4266-af96-d4fcb832a1e7",
	"config_id": "23b0eb2c-53cf-4e13-b516-416c38937d99",
	"type_id": null,
	"fields": {
		"display-name": {"data_type": "text_short", "localized": false, "value": "Test Speaker"},
		"email": {"data_type": "text_short", "localized": false, "value": null},
		"biography": {"data_type": "text_long", "localized": false, "value": "A non-empty biography for test purposes."},
		"use-gravatar": {"data_type": "boolean", "localized": false, "value": false},
		"photo": {"data_type": "image", "localized": false, "value": null},
	},
	"children": {},
	"overflow_data": {},
}

# ─── Patch builders ─────────────────────────────────────────────────────────────
# Each returns a single JSON-Patch array.  Pass a list of results to _mk.

_action(a)      := [{"op": "replace", "path": "/action",                                    "value": a}]
_transition(t)  := [{"op": "replace", "path": "/transition",                                "value": t}]
_field_path(f)  := [{"op": "replace", "path": "/field",                                     "value": f}]
_node_id(id)    := [{"op": "replace", "path": "/node_id",                                   "value": id}]
_as_user(u)     := [{"op": "replace", "path": "/user",                                      "value": u}]
_status(s)      := [{"op": "replace", "path": "/entity/fields/status/value",                "value": s}]
_as_owner(u)    := [{"op": "replace", "path": "/entity/fields/owner/value",                 "value": u}]
_field(slug, v) := [{"op": "replace", "path": sprintf("/entity/fields/%v/value", [slug]),   "value": v}]
_clear(slug)    := _field(slug, null)
_changed(f)     := [{"op": "replace", "path": "/changed_fields",                            "value": f}]
_children(k, v) := [{"op": "replace", "path": sprintf("/entity/children/%v", [k]),          "value": v}]

# ─── Document factory ───────────────────────────────────────────────────────────
# Accepts a list of patch arrays (from the builders above), flattens them, and
# applies the combined operation list to BASE_DOCUMENT in a single json.patch call.
#
# Usage:
#   _mk([_action("save"), _status("draft"), _as_user(USER_PHI1010)])

_mk(patch_lists) := json.patch(BASE_DOCUMENT, [p |
	some pl in patch_lists
	some p in pl
])

# ─── Tests: access control ──────────────────────────────────────────────────────

test_owner_can_view if {
	ev := udm with input as _mk([])
	ev.allow
	ev.error_messages == set()
}

test_stranger_cannot_view if {
	ev := udm with input as _mk([_as_user(USER_NONE)])
	not ev.allow
	some m in ev.error_messages
	m.level in {"error", "critical"}
}

test_moderator_can_view_submitted if {
	ev := udm with input as _mk([_as_user(USER_MODERATOR)])
	ev.allow
}

test_moderator_cannot_view_draft if {
	ev := udm with input as _mk([_as_user(USER_MODERATOR), _status("draft")])
	not ev.allow
}

# ─── Tests: save ────────────────────────────────────────────────────────────────

test_owner_can_save_draft if {
	ev := udm with input as _mk([_action("save"), _status("draft")])
	ev.allow
}

test_non_moderator_blocked_from_reviewer_assignment if {
	ev := udm with input as _mk([
		_action("save"),
		_as_user(USER_PHI1010),
		_changed({"requested-reviewer-groups": true}),
	])
	some m in ev.error_messages
	m.level == "critical"
	contains(m.text, "moderators")
}

# ─── Tests: submit transition ───────────────────────────────────────────────────

test_submit_blocked_when_checklist_incomplete if {
	# BASE_DOCUMENT has a speaker with biography: null — checklist is incomplete.
	ev := udm with input as _mk([
		_action("transition"),
		_transition("submit"),
		_field_path("status"),
	])
	some m in ev.error_messages
	m.level == "error"
	m.text == "All required fields must be completed before submitting."
}

test_submit_allowed_when_checklist_complete if {
	ev := udm with input as _mk([
		_action("transition"),
		_transition("submit"),
		_field_path("status"),
		_status("draft"),
		_children("speakers", [SPEAKER_WITH_BIO]),
	])
	ev.allow
	not {m | some m in ev.error_messages; m.level in {"error", "critical"}} != set()
}

# ─── Base document ──────────────────────────────────────────────────────────────

BASE_DOCUMENT := INPUT_DOCUMENT

INPUT_DOCUMENT := {
	"action": "view",
	"entity": {
		"id": "8e5366f4-5de9-4085-a255-bf80a68e13fd",
		"type": "entity",
		"config_version_id": "fee1ccdb-9dd6-4ee3-bd3a-1e3622a8f042",
		"config_id": "0f662758-8f16-45f9-bad7-bf91d6fbeb9c",
		"type_id": "e08720eb-1491-4920-9386-cc2fa520de25",
		"fields": {
			"proposal-id": {
				"data_type": "slug_id",
				"localized": false,
				"value": 4,
			},
			"title": {
				"data_type": "text_short",
				"localized": false,
				"value": "My submission",
			},
			"status": {
				"data_type": "workflow",
				"localized": false,
				"value": "submitted",
			},
			"owner": {
				"data_type": "user_select",
				"localized": false,
				"value": {
					"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
					"username": "admin",
					"email": "redacted@example.com",
					"phone_number": "",
					"is_active": true,
					"is_staff": true,
					"is_superuser": true,
					"groups": [
						{
							"id": 1,
							"name": "Authenticated Users",
							"members": [
								{
									"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
									"username": "admin",
									"email": "redacted@example.com",
									"phone_number": "",
									"is_active": true,
									"is_staff": true,
									"is_superuser": true,
									"groups": [
										{
											"id": 1,
											"name": "Authenticated Users",
										},
										{
											"id": 2,
											"name": "moderators",
										},
									],
								},
								{
									"id": "d2d9d14a-71ff-4055-8805-3f3cc74c267f",
									"username": "phi1010",
									"email": "redacted@example.com",
									"phone_number": "",
									"is_active": true,
									"is_staff": false,
									"is_superuser": false,
									"groups": [{
										"id": 1,
										"name": "Authenticated Users",
									}],
								},
							],
						},
						{
							"id": 2,
							"name": "moderators",
							"members": [{
								"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
								"username": "admin",
								"email": "redacted@example.com",
								"phone_number": "",
								"is_active": true,
								"is_staff": true,
								"is_superuser": true,
								"groups": [
									{
										"id": 1,
										"name": "Authenticated Users",
									},
									{
										"id": 2,
										"name": "moderators",
									},
								],
							}],
						},
					],
				},
			},
			"main-tabs": {
				"data_type": "tab_container",
				"localized": false,
				"value": null,
			},
			"tab-general": {
				"data_type": "tab",
				"localized": false,
				"value": null,
			},
			"submission-type": {
				"data_type": "select_single",
				"localized": false,
				"value": "course",
			},
			"area": {
				"data_type": "select_single",
				"localized": false,
				"value": "digital",
			},
			"language": {
				"data_type": "select_single",
				"localized": false,
				"value": "de",
			},
			"abstract": {
				"data_type": "text_long",
				"localized": false,
				"value": "apodj poqdßowq uwqudß921u ß921u ßueß 0ßu20ßuu ßdu21ß",
			},
			"description": {
				"data_type": "text_markdown",
				"localized": false,
				"value": "# apodj poqdßowq uwqudß921u ß921u ßueß 0ßu20ßuu ßdu21ß\n\n## apodj poqdßowq uwqudß921u ß921u ßueß 0ßu20ßuu ßdu21ß\n\napodj poqdßowq uwqudß921u ß921u ßueß 0ßu20ßuu ßdu21ß\n\n| apodj poqdßowq uwqudß921u ß921u ßueß 0ßu20ßuu ßdu21ß | apodj poqdßowq uwqudß921u ß921u ßueß 0ßu20ßuu ßdu21ß |\n| 1 | 2 |",
			},
			"photo-copyright-consent": {
				"data_type": "boolean",
				"localized": false,
				"value": true,
			},
			"photo": {
				"data_type": "image",
				"localized": false,
				"value": "286793dd-2918-4270-a9e3-68a598848c4a",
			},
			"nav-general": {
				"data_type": "hstack",
				"localized": false,
				"value": null,
			},
			"nav-general-right": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"save-button-4": {
				"data_type": "save_button",
				"localized": false,
				"value": null,
			},
			"next-general": {
				"data_type": "tab_next",
				"localized": false,
				"value": null,
			},
			"tab-participants": {
				"data_type": "tab",
				"localized": false,
				"value": null,
			},
			"is-basic-course": {
				"data_type": "boolean",
				"localized": false,
				"value": true,
			},
			"max-participants": {
				"data_type": "integer",
				"localized": false,
				"value": 2000,
			},
			"material-cost-eur": {
				"data_type": "float",
				"localized": false,
				"value": 20,
			},
			"nav-participants": {
				"data_type": "hstack",
				"localized": false,
				"value": null,
			},
			"nav-participants-left": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"prev-participants": {
				"data_type": "tab_prev",
				"localized": false,
				"value": null,
			},
			"nav-participants-right": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"save-button-3": {
				"data_type": "save_button",
				"localized": false,
				"value": null,
			},
			"next-participants": {
				"data_type": "tab_next",
				"localized": false,
				"value": null,
			},
			"tab-scheduling": {
				"data_type": "tab",
				"localized": false,
				"value": null,
			},
			"duration-days": {
				"data_type": "integer",
				"localized": false,
				"value": 20,
			},
			"duration-time-per-day": {
				"data_type": "text_short",
				"localized": false,
				"value": "20:00",
			},
			"occurrence-count": {
				"data_type": "integer",
				"localized": false,
				"value": 2,
			},
			"preferred-dates": {
				"data_type": "text_long",
				"localized": false,
				"value": "apodj poqdßowq uwqudß921u ß921u ßueß 0ßu20ßuu ßdu21ß",
			},
			"nav-scheduling": {
				"data_type": "hstack",
				"localized": false,
				"value": null,
			},
			"nav-scheduling-left": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"prev-scheduling": {
				"data_type": "tab_prev",
				"localized": false,
				"value": null,
			},
			"nav-scheduling-right": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"save-button-2": {
				"data_type": "save_button",
				"localized": false,
				"value": null,
			},
			"next-scheduling": {
				"data_type": "tab_next",
				"localized": false,
				"value": null,
			},
			"tab-people": {
				"data_type": "tab",
				"localized": false,
				"value": null,
			},
			"has-building-access": {
				"data_type": "boolean",
				"localized": false,
				"value": false,
			},
			"editors": {
				"data_type": "user_select_multi",
				"localized": false,
				"value": null,
			},
			"nav-people": {
				"data_type": "hstack",
				"localized": false,
				"value": null,
			},
			"nav-people-left": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"prev-people": {
				"data_type": "tab_prev",
				"localized": false,
				"value": null,
			},
			"nav-people-right": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"save-button": {
				"data_type": "save_button",
				"localized": false,
				"value": null,
			},
			"next-people": {
				"data_type": "tab_next",
				"localized": false,
				"value": null,
			},
			"tab-submission": {
				"data_type": "tab",
				"localized": false,
				"value": null,
			},
			"internal-notes": {
				"data_type": "text_long",
				"localized": false,
				"value": null,
			},
			"moderation-comment": {
				"data_type": "text_long",
				"localized": false,
				"value": null,
			},
			"requested-reviewer-groups": {
				"data_type": "group_select_multi",
				"localized": false,
				"value": [
					{
						"id": 1,
						"name": "Authenticated Users",
						"members": [
							{
								"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
								"username": "admin",
								"email": "redacted@example.com",
								"phone_number": "",
								"is_active": true,
								"is_staff": true,
								"is_superuser": true,
								"groups": [
									{
										"id": 1,
										"name": "Authenticated Users",
									},
									{
										"id": 2,
										"name": "moderators",
									},
								],
							},
							{
								"id": "d2d9d14a-71ff-4055-8805-3f3cc74c267f",
								"username": "phi1010",
								"email": "redacted@example.com",
								"phone_number": "",
								"is_active": true,
								"is_staff": false,
								"is_superuser": false,
								"groups": [{
									"id": 1,
									"name": "Authenticated Users",
								}],
							},
						],
					},
					{
						"id": 2,
						"name": "moderators",
						"members": [{
							"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
							"username": "admin",
							"email": "redacted@example.com",
							"phone_number": "",
							"is_active": true,
							"is_staff": true,
							"is_superuser": true,
							"groups": [
								{
									"id": 1,
									"name": "Authenticated Users",
									"members": [
										{
											"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
											"username": "admin",
											"email": "redacted@example.com",
											"phone_number": "",
											"is_active": true,
											"is_staff": true,
											"is_superuser": true,
											"groups": [
												{
													"id": 1,
													"name": "Authenticated Users",
												},
												{
													"id": 2,
													"name": "moderators",
												},
											],
										},
										{
											"id": "d2d9d14a-71ff-4055-8805-3f3cc74c267f",
											"username": "phi1010",
											"email": "redacted@example.com",
											"phone_number": "",
											"is_active": true,
											"is_staff": false,
											"is_superuser": false,
											"groups": [{
												"id": 1,
												"name": "Authenticated Users",
											}],
										},
									],
								},
								{
									"id": 2,
									"name": "moderators",
									"members": [{
										"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
										"username": "admin",
										"email": "redacted@example.com",
										"phone_number": "",
										"is_active": true,
										"is_staff": true,
										"is_superuser": true,
										"groups": [
											{
												"id": 1,
												"name": "Authenticated Users",
											},
											{
												"id": 2,
												"name": "moderators",
											},
										],
									}],
								},
							],
						}],
					},
				],
			},
			"requested-reviewer-users": {
				"data_type": "user_select_multi",
				"localized": false,
				"value": [
					{
						"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
						"username": "admin",
						"email": "redacted@example.com",
						"phone_number": "",
						"is_active": true,
						"is_staff": true,
						"is_superuser": true,
						"groups": [
							{
								"id": 1,
								"name": "Authenticated Users",
								"members": [
									{
										"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
										"username": "admin",
										"email": "redacted@example.com",
										"phone_number": "",
										"is_active": true,
										"is_staff": true,
										"is_superuser": true,
										"groups": [
											{
												"id": 1,
												"name": "Authenticated Users",
											},
											{
												"id": 2,
												"name": "moderators",
											},
										],
									},
									{
										"id": "d2d9d14a-71ff-4055-8805-3f3cc74c267f",
										"username": "phi1010",
										"email": "redacted@example.com",
										"phone_number": "",
										"is_active": true,
										"is_staff": false,
										"is_superuser": false,
										"groups": [{
											"id": 1,
											"name": "Authenticated Users",
										}],
									},
								],
							},
							{
								"id": 2,
								"name": "moderators",
								"members": [{
									"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
									"username": "admin",
									"email": "redacted@example.com",
									"phone_number": "",
									"is_active": true,
									"is_staff": true,
									"is_superuser": true,
									"groups": [
										{
											"id": 1,
											"name": "Authenticated Users",
										},
										{
											"id": 2,
											"name": "moderators",
										},
									],
								}],
							},
						],
					},
					{
						"id": "d2d9d14a-71ff-4055-8805-3f3cc74c267f",
						"username": "phi1010",
						"email": "redacted@example.com",
						"phone_number": "",
						"is_active": true,
						"is_staff": false,
						"is_superuser": false,
						"groups": [{
							"id": 1,
							"name": "Authenticated Users",
							"members": [
								{
									"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
									"username": "admin",
									"email": "redacted@example.com",
									"phone_number": "",
									"is_active": true,
									"is_staff": true,
									"is_superuser": true,
									"groups": [
										{
											"id": 1,
											"name": "Authenticated Users",
										},
										{
											"id": 2,
											"name": "moderators",
										},
									],
								},
								{
									"id": "d2d9d14a-71ff-4055-8805-3f3cc74c267f",
									"username": "phi1010",
									"email": "redacted@example.com",
									"phone_number": "",
									"is_active": true,
									"is_staff": false,
									"is_superuser": false,
									"groups": [{
										"id": 1,
										"name": "Authenticated Users",
									}],
								},
							],
						}],
					},
				],
			},
			"nav-submission": {
				"data_type": "hstack",
				"localized": false,
				"value": null,
			},
			"nav-submission-left": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"prev-submission": {
				"data_type": "tab_prev",
				"localized": false,
				"value": null,
			},
			"nav-submission-right": {
				"data_type": "hstack_group",
				"localized": false,
				"value": null,
			},
			"save-submission": {
				"data_type": "save_button",
				"localized": false,
				"value": null,
			},
			"speakers": {
				"data_type": "submodel_list",
				"localized": false,
				"value": null,
			},
			"reviews": {
				"data_type": "submodel_list",
				"localized": false,
				"value": null,
			},
		},
		"children": {
			"speakers": [{
				"id": "ef54157c-8f05-469d-91c2-873109212a11",
				"type": "submodel:speakers",
				"config_version_id": "f7951aee-3a82-4266-af96-d4fcb832a1e7",
				"config_id": "23b0eb2c-53cf-4e13-b516-416c38937d99",
				"type_id": null,
				"fields": {
					"display-name": {
						"data_type": "text_short",
						"localized": false,
						"value": null,
					},
					"email": {
						"data_type": "text_short",
						"localized": false,
						"value": null,
					},
					"biography": {
						"data_type": "text_long",
						"localized": false,
						"value": null,
					},
					"use-gravatar": {
						"data_type": "boolean",
						"localized": false,
						"value": false,
					},
					"photo": {
						"data_type": "image",
						"localized": false,
						"value": null,
					},
				},
				"children": {},
				"overflow_data": {},
				"created_at": "2026-06-04T15:32:00.597275+00:00",
				"updated_at": "2026-06-04T15:32:00.597285+00:00",
			}],
			"reviews": [{
				"id": "d4189c69-df54-4b6a-9a5d-c0d80ad04502",
				"type": "submodel:reviews",
				"config_version_id": "6a2c5e84-62d5-442c-94ff-be5d33635cb7",
				"config_id": "07af4d8d-6c5a-44b9-bfc1-ace4dc2aeec2",
				"type_id": null,
				"fields": {
					"author": {
						"data_type": "user_select",
						"localized": false,
						"value": null,
					},
					"vote": {
						"data_type": "workflow",
						"localized": false,
						"value": "open",
					},
					"comment": {
						"data_type": "text_long",
						"localized": false,
						"value": null,
					},
				},
				"children": {},
				"overflow_data": {},
				"created_at": "2026-06-04T15:40:04.216447+00:00",
				"updated_at": "2026-06-04T15:40:04.216453+00:00",
			}],
		},
		"overflow_data": {},
		"created_at": "2026-06-04T14:27:49.295195+00:00",
		"updated_at": "2026-06-04T14:27:49.295229+00:00",
	},
	"user": {
		"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
		"username": "admin",
		"email": "redacted@example.com",
		"phone_number": "",
		"is_active": true,
		"is_staff": true,
		"is_superuser": true,
		"groups": [
			{
				"id": 1,
				"name": "Authenticated Users",
				"members": [
					{
						"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
						"username": "admin",
						"email": "redacted@example.com",
						"phone_number": "",
						"is_active": true,
						"is_staff": true,
						"is_superuser": true,
						"groups": [
							{
								"id": 1,
								"name": "Authenticated Users",
							},
							{
								"id": 2,
								"name": "moderators",
							},
						],
					},
					{
						"id": "d2d9d14a-71ff-4055-8805-3f3cc74c267f",
						"username": "phi1010",
						"email": "redacted@example.com",
						"phone_number": "",
						"is_active": true,
						"is_staff": false,
						"is_superuser": false,
						"groups": [{
							"id": 1,
							"name": "Authenticated Users",
						}],
					},
				],
			},
			{
				"id": 2,
				"name": "moderators",
				"members": [{
					"id": "d4895d13-f968-4ff4-aa8d-e46a23649156",
					"username": "admin",
					"email": "redacted@example.com",
					"phone_number": "",
					"is_active": true,
					"is_staff": true,
					"is_superuser": true,
					"groups": [
						{
							"id": 1,
							"name": "Authenticated Users",
						},
						{
							"id": 2,
							"name": "moderators",
						},
					],
				}],
			},
		],
		"permissions": [],
	},
	"type_id": "e08720eb-1491-4920-9386-cc2fa520de25",
	"changed_fields": null,
	"transition": null,
	"field": null,
}
