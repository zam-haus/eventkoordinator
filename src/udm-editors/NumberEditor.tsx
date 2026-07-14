import { InputNumber } from 'primereact/inputnumber'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'

function makeNumberEditor(step: 'integer' | 'float') {
  return function NumberEditor({ value, onChange, disabled }: FieldInputProps) {
    return (
      <InputNumber className="p-inputtext-sm" inputClassName="p-inputtext-sm"
        value={value != null ? (value as number) : null}
        onChange={e => onChange(e.value ?? null)}
        useGrouping={false}
        minFractionDigits={step === 'float' ? 0 : undefined}
        maxFractionDigits={step === 'float' ? 10 : 0}
        disabled={disabled} />
    )
  }
}

const IntegerEditor = makeNumberEditor('integer')
const FloatEditor = makeNumberEditor('float')

fieldEditorRegistry.register('integer', IntegerEditor)
fieldEditorRegistry.register('float', FloatEditor)
