import { Calendar } from 'primereact/calendar'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'
import { browserTimeZone, parseAppDatetime } from '../timezone'

function pad(n: number): string { return String(n).padStart(2, '0') }

function parseDate(v: unknown): Date | null {
  if (!v) return null
  const d = new Date(v as string)
  return isNaN(d.getTime()) ? null : d
}

function parseDatetime(v: unknown): Date | null {
  if (!v || typeof v !== 'string') return null
  const d = parseAppDatetime(v, browserTimeZone())
  return isNaN(d.getTime()) ? null : d
}

function parseTime(v: unknown): Date | null {
  if (!v) return null
  const m = /^(\d{1,2}):(\d{2})/.exec(v as string)
  if (!m) return null
  const d = new Date()
  d.setHours(Number(m[1]), Number(m[2]), 0, 0)
  return d
}

function DateEditor({ value, onChange, disabled }: FieldInputProps) {
  return (
    <Calendar className="p-inputtext-sm" dateFormat="yy-mm-dd" showIcon
      value={parseDate(typeof value === 'string' ? value.slice(0, 10) : value)}
      onChange={e => onChange(e.value
        ? `${e.value.getFullYear()}-${pad(e.value.getMonth() + 1)}-${pad(e.value.getDate())}`
        : null)}
      disabled={disabled} />
  )
}

function TimeEditor({ value, onChange, disabled }: FieldInputProps) {
  return (
    <Calendar className="p-inputtext-sm" timeOnly
      value={parseTime(value)}
      onChange={e => onChange(e.value ? `${pad(e.value.getHours())}:${pad(e.value.getMinutes())}` : null)}
      disabled={disabled} />
  )
}

function DatetimeEditor({ value, onChange, disabled }: FieldInputProps) {
  // Timezone-aware: stored/transmitted as an offset ISO string (UTC, via
  // toISOString()) rather than a naive local wall-clock string, so a value
  // set by a user in one timezone displays correctly for a user in another
  // (see calendar_fetch / CalendarPreview, which read/write the same
  // convention). The picker itself still shows/edits in the browser's local
  // time — Date's getters/setters are always local, only the wire format
  // changed.
  return (
    <Calendar className="p-inputtext-sm" showTime dateFormat="yy-mm-dd" showIcon
      value={parseDatetime(value)}
      onChange={e => onChange(e.value ? e.value.toISOString() : null)}
      disabled={disabled} />
  )
}

fieldEditorRegistry.register('date', DateEditor)
fieldEditorRegistry.register('time', TimeEditor)
fieldEditorRegistry.register('datetime', DatetimeEditor)
