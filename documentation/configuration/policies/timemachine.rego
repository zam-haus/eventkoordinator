package udm.udmframeworkv1.modules.timemachine

import data.udm
import rego.v1

old_entity := input.input.old_entity if input.old_entity

else := input.entity

old_input := old_input if {
	old_input := input with input.entity as old_entity with input.changed_fields as [] with input.action as "view"
}
