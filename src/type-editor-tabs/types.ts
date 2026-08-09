import type { ComponentType } from 'react'

/** Props every type-editor tab component receives (registered or fallback).
 *  `configVersionId` is the ConfigVersion the tab's config is stored
 *  against — the same version the rest of the type editor (fields, policies)
 *  operates on. */
export interface TypeEditorTabProps {
  tabId: string
  configVersionId: string
}

export type TypeEditorTabComponent = ComponentType<TypeEditorTabProps>
