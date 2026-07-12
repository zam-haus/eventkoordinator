package udmtree

import rego.v1

# ─── Tree walker (§3.3-12) ────────────────────────────────────────────────────
# Every node document in the tree (root included), with its dotted path prefix
# ("" for the root, "reviews" for a review child, …) for highlight_fields.
# Lives OUTSIDE data.udm (package udmtree) so that neither the aggregator's
# dynamic modules[name] scan nor module imports create pseudo-recursive cycles.

tree_nodes_with_path contains [path_str, node] if {
	walk(input.entity, [path, node])
	is_object(node)
	node.schema_id
	slugs := [path[i + 1] |
		some i, seg in path
		seg == "children"
	]
	path_str := concat(".", slugs)
}

tree_nodes_with_path contains ["", input.entity]

tree_nodes contains node if {
	some [_, node] in tree_nodes_with_path
}
