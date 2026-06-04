import type { FieldDefinitionOut } from '../apiUdm'
import { fieldPreviewRegistry } from './registry-preview'

export interface PreviewProps {
  fd: FieldDefinitionOut
  value: unknown
  lang?: string
  entityChildren?: Record<string, unknown[]>
}

// Plain-text fallback — no React, suitable for CSV, titles, aria-labels, etc.
export function fieldPreviewText(fd: FieldDefinitionOut, value: unknown, lang = 'en'): string {
  if (value == null) return ''
  const tc = fd.type_config as Record<string, unknown> | null ?? {}
  switch (fd.data_type) {
    case 'boolean':
      return value ? 'Yes' : 'No'
    case 'slug_id': {
      const prefix = (tc['prefix'] as string) ?? ''
      return prefix ? `${prefix}-${value}` : String(value)
    }
    case 'select_multi':
    case 'user_select_multi':
    case 'group_select_multi':
    case 'entity_select_multi':
      return Array.isArray(value) ? (value as unknown[]).map(String).join(', ') : String(value)
    case 'workflow': {
      const wfDef = (fd as Record<string, unknown>)['workflow_version'] as { states?: Array<{ name: string; label: Record<string, string> }> } | null
      const state = wfDef?.states?.find(s => s.name === value)
      return state ? (state.label[lang] ?? state.label['en'] ?? String(value)) : String(value)
    }
    case 'submodel_list':
    case 'submodel_select':
      return ''
    case 'image':
    case 'file': {
      const f = value as Record<string, string> | null
      return f?.['original_name'] ?? f?.['url'] ?? ''
    }
    case 'text_markdown':
    case 'text_richtext':
    case 'text_short':
    case 'text_long':
      return typeof value === 'string' ? value : String(value)
    default:
      return typeof value === 'string' ? value : String(value)
  }
}

export function FieldPreview({ fd, value, lang, entityChildren }: PreviewProps) {
  const Renderer = fieldPreviewRegistry.get(fd.data_type)
  if (Renderer) return <Renderer fd={fd} value={value} lang={lang} entityChildren={entityChildren} />
  const text = fieldPreviewText(fd, value, lang)
  return <span style={{ fontSize: '0.9rem', color: '#374151' }}>{text || '—'}</span>
}
