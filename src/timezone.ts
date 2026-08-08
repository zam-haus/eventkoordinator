// datetime fields are timezone-aware: new values are always written as an
// offset ISO string (UTC, via Date.toISOString()). A bare naive string only
// occurs for legacy data predating that change — it's interpreted as
// DEFAULT_TIMEZONE wall-clock (matching Django's TIME_ZONE, default_settings.py),
// never silently reinterpreted as the viewer's own browser timezone, which
// could differ and would shift the displayed instant.
export const DEFAULT_TIMEZONE = 'Europe/Berlin'

function hasOffset(iso: string): boolean {
  return /Z$|[+-]\d{2}:?\d{2}$/.test(iso)
}

/** Interpret a naive "YYYY-MM-DDTHH:mm:ss"-ish wall-clock string as a moment
 *  in `timeZone`, returning the equivalent instant as a native Date. */
export function zonedTimeToUtc(naive: string, timeZone: string = DEFAULT_TIMEZONE): Date {
  const [datePart, timePart = '00:00:00'] = naive.replace(' ', 'T').split('T')
  const [y, mo, d] = datePart.split('-').map(Number)
  const [h, mi, s] = timePart.split(':').map(Number)
  const guess = Date.UTC(y, (mo || 1) - 1, d || 1, h || 0, mi || 0, s || 0)
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone, hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
  const parts: Record<string, string> = {}
  for (const p of dtf.formatToParts(new Date(guess))) parts[p.type] = p.value
  const asIfUtc = Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day),
    Number(parts.hour), Number(parts.minute), Number(parts.second),
  )
  return new Date(guess - (asIfUtc - guess))
}

/** Parse any `datetime` field value the app might encounter: an offset-aware
 *  ISO string parses natively (already unambiguous); a bare naive string
 *  falls back to `timeZone` (default: the user's current browser timezone
 *  when passed explicitly, otherwise Europe/Berlin — see DEFAULT_TIMEZONE). */
export function parseAppDatetime(value: string, timeZone: string = DEFAULT_TIMEZONE): Date {
  return hasOffset(value) ? new Date(value) : zonedTimeToUtc(value, timeZone)
}

/** The IANA name of the browser's own current timezone, when available. */
export function browserTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return undefined
  }
}
