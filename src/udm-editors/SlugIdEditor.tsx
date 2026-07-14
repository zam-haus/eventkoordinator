import { InputText } from 'primereact/inputtext'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'

function SlugIdEditor({ fd, value }: FieldInputProps) {
  const tc = fd.type_config as Record<string, unknown>
  const prefix = (tc['prefix'] as string) ?? ''
  const display = value != null ? `${prefix}-${value}` : '—'
  return (
    <InputText className="p-inputtext-sm" value={display} disabled readOnly
      style={{ fontFamily: 'monospace' }} />
  )
}

fieldEditorRegistry.register('slug_id', SlugIdEditor)
