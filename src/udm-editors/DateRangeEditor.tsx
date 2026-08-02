import { Calendar } from 'primereact/calendar'
import { useEffect, useState } from 'react'
import type React from 'react'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'

/**
 * DateRangeEditor — a multi-field widget proving the M:N FormElement↔DataField
 * binding (PLAN_split_form_tree_and_data_fields.md §F1).
 *
 * A `date_range` FormElement binds to TWO `date` DataFields via
 * FormElementBinding with role="from" and role="to". This editor renders a
 * single PrimeReact Calendar in selectionMode="range" (one control, two dates)
 * and splits the [from, to] array back to the two bound fields.
 *
 * The in-progress selection is held in local state so PrimeReact can complete
 * the two-click range flow without the parent re-rendering the Calendar (which
 * would reset its internal "waiting for the second click" state). The parent is
 * only notified once the range is complete (both dates picked); if the user
 * picks the end before the start, the two are flipped so from ≤ to.
 */
function pad(n: number): string { return String(n).padStart(2, '0') }
function toISO(d: Date | null): string | null {
  return d ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : null
}
function parseDate(v: unknown): Date | null {
  if (!v) return null
  const d = new Date(v as string)
  return isNaN(d.getTime()) ? null : d
}

export interface DateRangeInputProps extends FieldInputProps {
  /** Value of the "to" bound data field (role="to"). */
  toValue?: unknown
  /** Called when the "to" bound field changes. */
  onToChange?: (v: unknown) => void
}

function DateRangeEditor({ value, onChange, toValue, onToChange, disabled }: DateRangeInputProps) {
  const from = parseDate(value)
  const to = parseDate(toValue)
  // Local range state mirrors the Calendar's in-progress selection so the
  // two-click range flow isn't reset by parent re-renders between clicks.
  const [range, setRange] = useState<(Date | null)[]>([from, to])
  // Re-sync from props after a save roundtrip (entity refresh updates the
  // bound field values).
  useEffect(() => { setRange([parseDate(value), parseDate(toValue)]) }, [value, toValue])

  return (
    <Calendar className="p-inputtext-sm" dateFormat="yy-mm-dd" showIcon
      selectionMode="range"
      value={range}
      onChange={e => {
        const arr = (e.value as (Date | null)[] | null) ?? [null, null]
        let f = arr[0] ?? null
        let t = arr[1] ?? null
        // Range complete: flip so from ≤ to.
        if (f && t && t.getTime() < f.getTime()) { [f, t] = [t, f] }
        const next: (Date | null)[] = [f, t]
        setRange(next)
        // Only propagate to the parent once the range is complete (both dates
        // set) or fully cleared — never mid-selection, which would wipe the
        // other bound field and reset PrimeReact's second-click state.
        if (f && t) {
          onChange(toISO(f))
          ;(onToChange ?? onChange)(toISO(t))
        } else if (!f && !t) {
          onChange(null)
          ;(onToChange ?? onChange)(null)
        }
      }}
      disabled={disabled} placeholder="Select date range" readOnlyInput />
  )
}

// Register under the date_range element type so the registry knows the widget.
fieldEditorRegistry.register('date_range', DateRangeEditor as unknown as React.ComponentType<FieldInputProps>)

export { DateRangeEditor }
