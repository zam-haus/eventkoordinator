import { InputText } from 'primereact/inputtext'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'
import styles from '../UdmEntityEditor.module.css'

function TextShortEditor({ fd, value, onChange, disabled }: FieldInputProps) {
  const tc = fd.type_config as Record<string, unknown>
  const maxLen = tc['max_length'] as number | undefined
  const len = ((value as string) ?? '').length
  const over = maxLen !== undefined && len > maxLen
  return (
    <div className={styles.editorStack}>
      <InputText className="p-inputtext-sm" value={(value as string) ?? ''}
        onChange={e => onChange(e.target.value)} disabled={disabled}
        maxLength={maxLen} />
      <div className={`${styles.lenHint}${over ? ` ${styles.lenHintOver}` : ''}`}>
        {maxLen !== undefined ? `${len} / ${maxLen}` : len}
      </div>
    </div>
  )
}

fieldEditorRegistry.register('text_short', TextShortEditor)
