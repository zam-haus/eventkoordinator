import { InputText } from 'primereact/inputtext'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'

export function FieldInput(props: FieldInputProps) {
  const Editor = fieldEditorRegistry.get(props.fd.data_type)
  if (Editor) return <Editor {...props} />
  return (
    <InputText className="p-inputtext-sm" value={JSON.stringify(props.value) ?? ''}
      onChange={e => { try { props.onChange(JSON.parse(e.target.value)) } catch { props.onChange(e.target.value) } }}
      disabled={props.disabled} />
  )
}
