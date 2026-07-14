import { InputTextarea } from 'primereact/inputtextarea'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'
import styles from '../UdmEntityEditor.module.css'

function TextRichtextEditor({ value, onChange, disabled }: FieldInputProps) {
  const len = ((value as string) ?? '').length
  return (
    <div className={styles.editorStack}>
      <InputTextarea className="p-inputtext-sm" rows={6} value={(value as string) ?? ''}
        onChange={e => onChange(e.target.value)} disabled={disabled}
        style={{ fontFamily: 'inherit' }} />
      <div className={styles.lenHint}>{len}</div>
    </div>
  )
}

fieldEditorRegistry.register('text_richtext', TextRichtextEditor)
