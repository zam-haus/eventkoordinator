import { useEffect, useState } from 'react'
import { CalendarPreview } from './udm-editors/CalendarPreview'
import { udmListCalendarSources, type CalendarSourceOut } from './apiUdm'

/** events-and-sync.md §6/Step 9: standalone dashboard calendar — lets a user
 *  pick which sync_core CalendarSources to overlay. UDM-type sources are not
 *  offered here (unlike the `calendar` form element, which is configured per
 *  type in the type editor) since there's no generic way to guess which
 *  start/end field pair a given type wants shown on a global view. */
export function UdmCalendarPage() {
  const [sources, setSources] = useState<CalendarSourceOut[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())

  useEffect(() => {
    udmListCalendarSources().then(rows => {
      setSources(rows)
      setSelected(new Set(rows.map(r => r.key)))
    })
  }, [])

  const toggle = (key: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div style={{ padding: '1.5rem', maxWidth: 1100, margin: '0 auto' }}>
      <h1 style={{ fontSize: '1.4rem', marginBottom: '1rem' }}>Calendar</h1>
      {sources.length > 0 && (
        <div style={{ display: 'flex', gap: '0.8rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          {sources.map(s => (
            <label key={s.key} style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.88rem' }}>
              <input type="checkbox" checked={selected.has(s.key)} onChange={() => toggle(s.key)} />
              {s.name}
            </label>
          ))}
        </div>
      )}
      <CalendarPreview sources={[...selected].map(key => `source:${key}`)} />
    </div>
  )
}
