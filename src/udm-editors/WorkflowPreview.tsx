import type { WorkflowVersionOut, FieldDefinitionOut } from '../apiUdm'
import type { PreviewProps } from './FieldPreview'
import { getLang } from './types'
import { fieldPreviewRegistry } from './registry-preview'

function WorkflowPreview({ fd, value, lang = 'en' }: PreviewProps) {
  if (value == null) return <span style={{ color: '#9ca3af' }}>—</span>
  const stateName = value as string
  const wfDef = (fd as FieldDefinitionOut & { workflow_version?: WorkflowVersionOut | null }).workflow_version
  const state = wfDef?.states.find(s => s.name === stateName)
  const label = state ? getLang(state.label as Record<string, string>, lang) || stateName : stateName
  const hasBg = state && state.background_color && state.background_color !== '#ffffff'
  const bg = hasBg ? state!.background_color : '#f1f5f9'
  const fg = hasBg ? state!.text_color : '#374151'
  const border = hasBg ? state!.background_color : '#d1d5db'

  return (
    <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: '999px', fontSize: '0.82rem', fontWeight: 600, background: bg, color: fg, border: `1px solid ${border}`, letterSpacing: '0.01em' }}>
      {label}
    </span>
  )
}

fieldPreviewRegistry.register('workflow', WorkflowPreview)
