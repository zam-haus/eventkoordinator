import { Checkbox } from 'primereact/checkbox'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'
import styles from '../UdmEntityEditor.module.css'

function BooleanEditor({ value, onChange, disabled }: FieldInputProps) {
  return (
    <label className={styles.checkbox}>
      <Checkbox checked={!!value}
        onChange={e => onChange(!!e.checked)}
        disabled={disabled} />
      Yes
    </label>
  )
}

fieldEditorRegistry.register('boolean', BooleanEditor)
