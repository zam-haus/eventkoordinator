package udm.udmframeworkv1.modules.structural

import data.udm.udmframeworkv1.modules.config
import data.udm.udmframeworkv1.modules.proposals._can_view
import data.udmtree
import rego.v1

# ─────────────────────────────────────────────────────────────────────────────
# Structural controls (config.STRUCTURAL_TYPES) are always VIEWABLE
# (view.rego); whether they are CLICKABLE is THIS module's editable grant.
# The GUI disables any structural button whose slug is not granted.
# ─────────────────────────────────────────────────────────────────────────────

# Structural fields are ALWAYS viewable, for every node and every user: they
# carry no entity data. (Moved here from view.rego so visibility and
# clickability of structural controls live in one module.)
viewable_fields contains {"node": node.id, "field": f} if {
	some node in udmtree.tree_nodes
	some f, entry in node.fields
	entry.data_type in config.STRUCTURAL_TYPES
}

# ALL structural controls — tabs, navigation, and save buttons alike — are
# clickable for everyone who may view the entity. Save buttons need no policy
# gate of their own: the actual save is authorized by the "save" evaluation at
# PATCH time (deny => 422, rollback), and the GUI additionally disables the
# button from the validation preview (save.valid, dirty state, blocking
# messages). Navigation is not a write at all.
editable_fields contains {"node": node.id, "field": f} if {
	_can_view
	some node in udmtree.tree_nodes
	some f, entry in node.fields
	entry.data_type in config.STRUCTURAL_TYPES
}
