import { createContext, useContext } from 'react'

// §6 submodel operation grants from the policy result (deny-by-default).
// Provided by UdmEntityEditor from the entity response / validation preview;
// consumed by SubmodelEditor for create/delete button state and the
// not-yet-saved item form.

export interface NewItemGrant {
  viewable: string[]
  editable: string[]
}

export interface UdmGrants {
  /** Child node ids the user may delete. */
  deletableNodes: string[]
  /** parent node id → submodel_list slug → new-item field grant.
   *  A PRESENT slug key means the user may create an item in that list. */
  creatableSubmodels: Record<string, Record<string, NewItemGrant>>
  /** Per-node editable field map {node_id: [slugs]} — a node with no entry
   *  (or an empty list) is not editable. */
  editableFields: Record<string, string[]>
}

export const UdmGrantsContext = createContext<UdmGrants | null>(null)

/** null = no grant context (e.g. admin previews) → legacy `disabled` behavior. */
export function useUdmGrants(): UdmGrants | null {
  return useContext(UdmGrantsContext)
}
