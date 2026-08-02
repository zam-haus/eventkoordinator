# ─────────────────────────────────────────────────────────────────────────────
# Input contract (draft — see documentation/rego-engine-review.md §3.3-11)
#
# The contract is expressed executably, not as prose:
#   * valid_input_doc(doc)  — the assertable predicate (valid_input applies it
#     to the live input; the framework should emit a critical error when it
#     fails, so engine/framework version skew fails closed).
#   * example_inputs        — GENERATED example documents covering all
#     combinations of action × field variant × children variant × transition
#     descriptor variant (hierarchy depth limited to root + one child level).
#   * examples_valid        — self-check: every generated example satisfies
#     valid_input_doc. Keep it true.
#
# Target contract — incorporates the review decisions:
#   §3.1-1  fixed result schema per entity action; per-node viewable/editable
#           maps; additional_result carry-over; type_result for
#           public_type_fields
#   §3.1-3  typed input + input_version
#   §3.2-7  flat lookup maps input.users / input.groups (PKs stay in fields)
#   §3.2-8  full submodel-tree in entity.children; entity links resolved one
#           deep into input.linked_entities
#   §3.3-12 schema_id on every node + flat input.schemas map
#   §4      single "preview" action with candidate_transitions descriptors
# ─────────────────────────────────────────────────────────────────────────────
package udm.udmframeworkv1.input_schema

import rego.v1

INPUT_VERSION := 1

# One evaluation per request, always on the ROOT entity document (never per
# node). "preview" replaces the removed validate_only modes and yields the
# save verdict, all messages, and the transition matrix in one pass.
ACTIONS := {
	"view",               # GET entity / history
	"browse",             # entity search (per candidate entity)
	"create",
	"save",               # PATCH, evaluated post-write inside the transaction
	"delete",
	"transition",         # POST transition, evaluated post-patch, pre-commit
	"preview",            # POST validation-preview (save + transition matrix)
	"public_type_fields", # type-level metadata; minimal input (action, user)
}

# ── Output aggregates ────────────────────────────────────────────────────────
# Every ENTITY action (view/browse/create/save/delete/transition/preview)
# is answered by exactly one object with a FIXED schema — unused keys are
# present with empty defaults, never absent:
#   data.udm.result := {
#     "allow":             <bool>,
#     "messages":          [<Message>],
#     "viewable_fields":   {"<node_id>": ["<slug>"]},  # PER NODE, whole tree
#                                             # filtered in this one pass;
#                                             # always maps/lists, never null:
#                                             # only listed fields are visible
#                                             # (deny-by-default)
#     "editable_fields":   {"<node_id>": ["<slug>"]},  # per node, same shape
#     "valid_transitions": [{"node","field","name"}],
#     "deletable_nodes":   ["<child_node_id>"],          # §6: delete buttons
#     "creatable_submodels": {"<parent_id>": {"<slug>":  # §6: key present =
#         {"viewable": [..], "editable": [..]}}},        # may create; value =
#                                                        # new-item field grant
#     "actions":           [<ActionObject>],
#     "dashboard_columns": [<ColumnObject>],
#     "additional_result": <object>,          # policy-defined carry-over: the
#                                             # VIEW pass's additional_result is
#                                             # fed back as input.additional_result
#                                             # to the save/transition/preview pass
#   }
# protected_fields is NOT part of the contract: it is an internal convention
# between modules and the framework defaults (save.rego / view.rego subtract
# it); the engine never reads it.
# "public_type_fields" reads a SEPARATE aggregate so type metadata never
# alters the entity-action result schema:
#   data.udm.type_result := {
#     "public_type_fields": {<slug>: <value>},
#     "type_description":   {"<lang>": "<markdown>"},
#   }
# Message levels: critical | error | warning | info | debug
#     critical => deny everything; error => deny transitions.

# ── Assertable predicates ────────────────────────────────────────────────────

valid_input if valid_input_doc(input)

# public_type_fields: minimal input — no entity context.
valid_input_doc(doc) if {
	doc.input_version == INPUT_VERSION
	doc.action == "public_type_fields"
	is_string(doc.type_id)
	is_object(doc.user)
	valid_locale(doc)
}

# locale: the requesting user's locale for message-language selection.
# null EXACTLY when the system calls itself (policy actions, background
# tasks) — policies must then fall back to a default language.
valid_locale(doc) if doc.locale == null

valid_locale(doc) if is_string(doc.locale)

valid_input_doc(doc) if {
	doc.input_version == INPUT_VERSION
	doc.action in ACTIONS
	doc.action != "public_type_fields"
	is_string(doc.type_id) # NEVER null: typeless entities are denied before Rego runs
	valid_entity_tree(doc)
	is_object(doc.schemas)
	is_object(doc.users)
	is_object(doc.groups)
	is_object(doc.linked_entities) # entity_select targets, ONE entity deep;
	                               # their own entity links stay raw PKs
	is_object(doc.files)           # image/file attachment metadata by PK:
	                               # {id, original_name, mime_type, size_bytes,
	                               #  image_width, image_height} — dimensions are
	                               #  null for non-images / unreadable files
	is_object(doc.user)
	is_object(doc.changed_fields)  # save/preview: raw submitted payload,
	                               # {"<slug>": {"value": <scalar>}}
	is_object(doc.additional_result) # result.additional_result of the VIEW
	                                 # pre-check on old_entity; {} if none ran
	valid_locale(doc)
	valid_old_entity(doc)
	valid_action_extras(doc)
}

# Rego forbids recursive rules, so the tree is flattened with walk():
# every object carrying a schema_id anywhere under doc.entity (including
# the root itself) is a node and must satisfy valid_node.
tree_nodes_of(doc) := {node |
	walk(doc.entity, [_, node])
	is_object(node)
	node.schema_id
}

# Convenience for modules: the flattened tree of the live input.
tree_nodes contains node if {
	some node in tree_nodes_of(input)
}

valid_entity_tree(doc) if {
	valid_node(doc, doc.entity) # the root is a node
	every node in tree_nodes_of(doc) {
		valid_node(doc, node)
	}
}

valid_node(doc, node) if {
	is_string(node.id)
	is_string(node.schema_id)
	object.get(doc.schemas, node.schema_id, null) != null
	is_object(node.fields)
	is_object(node.children)
}

# old_entity: null EXACTLY for view/browse/delete/create (nothing was
# patched / nothing persisted yet); for save/transition/preview it is ALWAYS
# a node document — even with empty changed_fields, where its content equals
# doc.entity. (Key absent for public_type_fields.)
valid_old_entity(doc) if {
	doc.action in {"view", "browse", "delete", "create"}
	doc.old_entity == null
}

valid_old_entity(doc) if {
	doc.action in {"save", "transition", "preview"}
	valid_node(doc, doc.old_entity)
}

valid_action_extras(doc) if {
	not doc.action in {"transition", "preview"}
}

valid_action_extras(doc) if {
	doc.action == "transition"
	is_string(doc.transition)
	is_string(doc.field)   # workflow field slug
	is_string(doc.node_id) # node owning the workflow field
	valid_descriptor(doc.transition_descriptor)
}

valid_action_extras(doc) if {
	doc.action == "preview"
	every _, wf_fields in doc.candidate_transitions {
		every _, wf in wf_fields {
			every _, descriptor in wf.transitions {
				valid_descriptor(descriptor)
			}
		}
	}
}

valid_descriptor(d) if {
	is_string(d.to_state)
	is_boolean(d.from_undefined_only)
	is_object(d.properties) # free-form, per WorkflowTransition + version merge
}

# ── Generated example documents ──────────────────────────────────────────────
# All combinations, depth-limited: the tree is root + at most ONE child level
# (children of children are always empty).

EXAMPLE_ROOT_SCHEMA := "11111111-1111-1111-1111-111111111111"

EXAMPLE_CHILD_SCHEMA := "22222222-2222-2222-2222-222222222222"

EXAMPLE_TYPE_ID := "33333333-3333-3333-3333-333333333333"

EXAMPLE_LINKED_ENTITY_ID := "77777777-7777-7777-7777-777777777777"

EXAMPLE_ATTACHMENT_ID := "88888888-8888-8888-8888-888888888888"

# The root schema exercises EVERY data type (plus one localized field,
# "description"). Values in nodes follow the lookup-map contract: user/group/
# entity selects hold raw PKs — resolve via input.users / input.groups /
# input.linked_entities; submodel_select/submodel_list children live in
# node.children.
example_schema_field_types := {
	"title": "text_short",
	"summary": "text_long",
	"body-markdown": "text_markdown",
	"body-richtext": "text_richtext",
	"count": "integer",
	"score": "float",
	"is-public": "boolean",
	"due-date": "date",
	"alarm-time": "time",
	"published-at": "datetime",
	"category": "select_single",
	"tags": "select_multi",
	"logo": "image",
	"attachment": "file",
	"owner": "user_select",
	"editors": "user_select_multi",
	"owner-group": "group_select",
	"editor-groups": "group_select_multi",
	"primary-review": "submodel_select",
	"related": "entity_select",
	"related-multi": "entity_select_multi",
	"proposal-id": "slug_id",
	"status": "workflow",
	"reviews": "submodel_list",
	"description": "text_long", # the localized field
}

example_schemas := {
	EXAMPLE_ROOT_SCHEMA: {
		"slug": "proposal",
		"fields": {slug: {"data_type": dt} | some slug, dt in example_schema_field_types},
		"properties": {"public": false},
	},
	EXAMPLE_CHILD_SCHEMA: {
		"slug": "review",
		"fields": {"vote": {"data_type": "select_single"}},
		"properties": {},
	},
}

example_user := {
	"id": "44444444-4444-4444-4444-444444444444",
	"username": "alice",
	"email": "alice@example.org",
	"phone_number": "",
	"is_active": true,
	"is_staff": false,
	"is_superuser": false,
	# session-scoped superuser sudo toggle; only ever true on input.user
	"sudo": false,
	"groups": [{"id": 1, "name": "moderators"}],
	"permissions": ["view_entity"],
}

example_users := {example_user.id: example_user}

example_groups := {"1": {"id": 1, "name": "moderators", "member_ids": [example_user.id]}}

# image/file fields hold raw attachment PKs; resolve metadata via input.files.
example_files := {EXAMPLE_ATTACHMENT_ID: {
	"id": EXAMPLE_ATTACHMENT_ID,
	"original_name": "logo.png",
	"mime_type": "image/png",
	"size_bytes": 123456,
	"image_width": 1920,
	"image_height": 1080,
}}

# The complete field map: one value example per data type. EVERY defined
# field appears (unset ones with value null / {} when localized).
example_base_fields := {
	"title": {"data_type": "text_short", "localized": false, "value": "Example title"},
	"summary": {"data_type": "text_long", "localized": false, "value": "A longer example summary."},
	"body-markdown": {"data_type": "text_markdown", "localized": false, "value": "# Heading\n\nBody."},
	"body-richtext": {"data_type": "text_richtext", "localized": false, "value": "<p>Body.</p>"},
	"count": {"data_type": "integer", "localized": false, "value": 42},
	"score": {"data_type": "float", "localized": false, "value": 4.2},
	"is-public": {"data_type": "boolean", "localized": false, "value": true},
	"due-date": {"data_type": "date", "localized": false, "value": "2026-01-31"},
	"alarm-time": {"data_type": "time", "localized": false, "value": "09:30:00"},
	"published-at": {"data_type": "datetime", "localized": false, "value": "2026-01-01T09:30:00+00:00"},
	"category": {"data_type": "select_single", "localized": false, "value": "hardware"},
	"tags": {"data_type": "select_multi", "localized": false, "value": ["alpha", "beta"]},
	"logo": {"data_type": "image", "localized": false, "value": EXAMPLE_ATTACHMENT_ID},
	"attachment": {"data_type": "file", "localized": false, "value": EXAMPLE_ATTACHMENT_ID},
	# raw PKs — resolve via the lookup maps:
	"owner": {"data_type": "user_select", "localized": false, "value": example_user.id},
	"editors": {"data_type": "user_select_multi", "localized": false, "value": [example_user.id]},
	"owner-group": {"data_type": "group_select", "localized": false, "value": 1},
	"editor-groups": {"data_type": "group_select_multi", "localized": false, "value": [1]},
	"primary-review": {"data_type": "submodel_select", "localized": false, "value": "child-1"},
	"related": {"data_type": "entity_select", "localized": false, "value": EXAMPLE_LINKED_ENTITY_ID},
	"related-multi": {"data_type": "entity_select_multi", "localized": false, "value": [EXAMPLE_LINKED_ENTITY_ID]},
	"proposal-id": {"data_type": "slug_id", "localized": false, "value": 7},
	"status": {"data_type": "workflow", "localized": false, "value": "draft"},
	"reviews": {"data_type": "submodel_list", "localized": false, "value": null}, # no scalar; nodes in children
	"description": {"data_type": "text_long", "localized": true, "value": {"en": "Example", "de": "Beispiel"}},
}

# Variants exercise the title field: plain scalar, localized dict, unset.
example_field_variants := {
	"plain": example_base_fields,
	"localized": object.union(example_base_fields, {"title": {"data_type": "text_short", "localized": true, "value": {"en": "Example", "de": "Beispiel"}}}),
	"unset": object.union(example_base_fields, {"title": {"data_type": "text_short", "localized": false, "value": null}}),
}

example_child := {
	"id": "child-1",
	"schema_id": EXAMPLE_CHILD_SCHEMA,
	"fields": {"vote": {"data_type": "select_single", "localized": false, "value": "accept"}},
	"children": {}, # depth limit: no grandchildren
	"overflow_data": {},
	"created_at": "2026-01-01T00:00:00Z",
	"updated_at": "2026-01-01T00:00:00Z",
}

example_children_variants := {
	"leaf": {},
	"with_child": {"reviews": [example_child]},
}

# entity_select target, resolved ONE entity deep: its own entity links
# ("related", "related-multi") stay raw PKs and are NOT in linked_entities.
example_linked_entity := {
	"id": EXAMPLE_LINKED_ENTITY_ID,
	"schema_id": EXAMPLE_ROOT_SCHEMA,
	"config_version_id": "55555555-5555-5555-5555-555555555555",
	"config_id": "66666666-6666-6666-6666-666666666666",
	"type_id": EXAMPLE_TYPE_ID,
	"fields": example_base_fields,
	"children": {},
	"overflow_data": {},
	"created_at": "2026-01-01T00:00:00Z",
	"updated_at": "2026-01-01T00:00:00Z",
}

example_linked_entities := {EXAMPLE_LINKED_ENTITY_ID: example_linked_entity}

example_descriptor_variants := {
	"normal": {"from_state": "draft", "to_state": "submitted", "from_undefined_only": false, "properties": {"moderator_only": false}},
	"initial": {"from_state": null, "to_state": "draft", "from_undefined_only": true, "properties": {}},
}

example_entity(field_variant, children_variant) := {
	"id": "root-1",
	"schema_id": EXAMPLE_ROOT_SCHEMA,
	"config_version_id": "55555555-5555-5555-5555-555555555555",
	"config_id": "66666666-6666-6666-6666-666666666666",
	"type_id": EXAMPLE_TYPE_ID,
	"fields": example_field_variants[field_variant],
	"children": example_children_variants[children_variant],
	"overflow_data": {},
	"created_at": "2026-01-01T00:00:00Z",
	"updated_at": "2026-01-01T00:00:00Z",
}

example_old_entity(action, _) := null if {
	action in {"view", "browse", "delete", "create"}
}

example_old_entity(action, entity) := entity if {
	action in {"save", "transition", "preview"}
}

example_extras(action, _) := {} if {
	not action in {"save", "transition", "preview"}
}

example_extras("save", _) := {"changed_fields": {"title": {"value": "New title"}}}

example_extras("transition", descriptor_variant) := {
	"transition": "submit",
	"field": "status",
	"node_id": "root-1",
	"transition_descriptor": example_descriptor_variants[descriptor_variant],
}

example_extras("preview", descriptor_variant) := {
	"changed_fields": {"title": {"value": "New title"}},
	"candidate_transitions": {"root-1": {"status": {
		"current_state": "draft",
		"transitions": {"submit": example_descriptor_variants[descriptor_variant]},
	}}},
}

# "de": a user request; null: the system calling itself.
example_locale_variants := {"de", null}

example_inputs contains doc if {
	some action in ACTIONS
	action != "public_type_fields"
	some field_variant, _ in example_field_variants
	some children_variant, _ in example_children_variants
	some descriptor_variant, _ in example_descriptor_variants
	some locale in example_locale_variants
	entity := example_entity(field_variant, children_variant)
	doc := object.union(
		{
			"input_version": INPUT_VERSION,
			"action": action,
			"locale": locale,
			"type_id": EXAMPLE_TYPE_ID,
			"entity": entity,
			"old_entity": example_old_entity(action, entity),
			"schemas": example_schemas,
			"users": example_users,
			"groups": example_groups,
			"linked_entities": example_linked_entities,
			"files": example_files,
			"user": example_user,
			"changed_fields": {},
			"additional_result": {},
		},
		example_extras(action, descriptor_variant),
	)
}

example_inputs contains doc if {
	some locale in example_locale_variants
	doc := {
		"input_version": INPUT_VERSION,
		"action": "public_type_fields",
		"locale": locale,
		"type_id": EXAMPLE_TYPE_ID,
		"user": example_user,
	}
}

# Self-check: every generated example satisfies the contract.
examples_valid if {
	every doc in example_inputs {
		valid_input_doc(doc)
	}
}
