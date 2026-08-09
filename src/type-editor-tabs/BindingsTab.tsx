import { useEffect, useState } from 'react'
import { Dropdown } from 'primereact/dropdown'
import { InputText } from 'primereact/inputtext'
import { InputTextarea } from 'primereact/inputtextarea'
import { udmGetTypeEditorTabConfig, udmPutTypeEditorTabConfig, UdmApiError } from '../apiUdm'
import type { TypeEditorTabProps } from './types'
import styles from '../UdmAdminPage.module.css'

type SourceKind = 'effective' | 'field' | 'template'

interface BindingSource {
  effective?: string
  field?: string
  template?: string
}

interface SubmodelSpec {
  submodel: string
  start: string
  end?: string | null
}

interface BindingsConfig {
  bindings?: Record<string, BindingSource>
  submodel?: SubmodelSpec | null
}

interface Row {
  property: string
  kind: SourceKind
  value: string
}

const REMOTE_PROPERTIES: Record<string, string[]> = {
  sync_caldav: ['SUMMARY', 'LOCATION', 'DESCRIPTION', 'DTSTART', 'DTEND'],
  sync_ical: ['SUMMARY', 'LOCATION', 'DESCRIPTION', 'DTSTART', 'DTEND'],
}

function sourceToRow(property: string, source: BindingSource | undefined): Row {
  if (source?.field !== undefined && source.field !== null) return { property, kind: 'field', value: source.field }
  if (source?.template !== undefined && source.template !== null) return { property, kind: 'template', value: source.template }
  return { property, kind: 'effective', value: source?.effective ?? '' }
}

function rowToSource(row: Row): BindingSource {
  return { [row.kind]: row.value }
}

const KIND_OPTIONS: { label: string, value: SourceKind }[] = [
  { label: 'Effective key', value: 'effective' },
  { label: 'Data field slug', value: 'field' },
  { label: 'Jinja template', value: 'template' },
]

const KIND_HELP: Record<SourceKind, string> = {
  effective: 'Key into the policy’s effective object (coalesced overrides).',
  field: 'Raw stored value of this data field slug, no policy involved.',
  template: 'Jinja string rendered with { effective, entity } context, e.g. "{{ effective.title }} — {{ effective.room }}".',
}

/** Dedicated editor for the field-binding tabs registered by sync_caldav /
 *  sync_ical (events-and-sync.md §13.2): an ordered map
 *  `remote_property -> source`, where a source is one of effective key /
 *  data field slug / Jinja template.
 *
 *  Every remote property this plugin knows about (`REMOTE_PROPERTIES`) is
 *  always shown as a fixed row — no add/remove UI, since the property list
 *  is a closed set per plugin, not admin-defined. Leaving a row's value
 *  blank is valid: an empty/unresolved binding is simply ignored at sync
 *  time (events-and-sync.md §14), it doesn't block saving the tab.
 *
 *  §13.3: a type can also fan out — push one remote VEVENT per child of a
 *  submodel_list field (e.g. a Timeslot child) instead of one for the whole
 *  entity. This is the answer to "how do I create multiple VEVENTs from one
 *  entity": bind SUMMARY/LOCATION/DESCRIPTION above as usual (shared across
 *  every VEVENT) and set the submodel spec below instead of binding
 *  DTSTART/DTEND directly — each child's own start/end field fills those
 *  per VEVENT. A Jinja template or effective/field source only ever
 *  produces one value per entity, so on its own it cannot create more than
 *  one VEVENT — the submodel spec is what fans out. */
export function BindingsTab({ tabId, configVersionId }: TypeEditorTabProps) {
  const [rows, setRows] = useState<Row[]>([])
  const [fanOutEnabled, setFanOutEnabled] = useState(false)
  const [submodelSlug, setSubmodelSlug] = useState('')
  const [startSlug, setStartSlug] = useState('')
  const [endSlug, setEndSlug] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const properties = REMOTE_PROPERTIES[tabId] ?? []
    udmGetTypeEditorTabConfig(configVersionId, tabId)
      .then(res => {
        if (cancelled) return
        const config = res.config as BindingsConfig
        const bindings = config.bindings ?? {}
        setRows(properties.map(property => sourceToRow(property, bindings[property])))
        if (config.submodel) {
          setFanOutEnabled(true)
          setSubmodelSlug(config.submodel.submodel)
          setStartSlug(config.submodel.start)
          setEndSlug(config.submodel.end ?? '')
        } else {
          setFanOutEnabled(false)
        }
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load tab config') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tabId, configVersionId])

  async function save(next: { rows?: Row[], fanOutEnabled?: boolean, submodelSlug?: string, startSlug?: string, endSlug?: string }) {
    const nextRows = next.rows ?? rows
    const nextFanOutEnabled = next.fanOutEnabled ?? fanOutEnabled
    const nextSubmodelSlug = next.submodelSlug ?? submodelSlug
    const nextStartSlug = next.startSlug ?? startSlug
    const nextEndSlug = next.endSlug ?? endSlug

    setError(null)
    setSuccess(null)
    setSaving(true)
    const properties = REMOTE_PROPERTIES[tabId] ?? []
    const bindings: Record<string, BindingSource> = {}
    for (const row of nextRows) bindings[row.property] = rowToSource(row)
    const submodel = nextFanOutEnabled
      ? { submodel: nextSubmodelSlug, start: nextStartSlug, end: nextEndSlug || null }
      : null
    try {
      const res = await udmPutTypeEditorTabConfig(configVersionId, tabId, { bindings, submodel })
      const config = res.config as BindingsConfig
      const savedBindings = config.bindings ?? {}
      setRows(properties.map(property => sourceToRow(property, savedBindings[property])))
      if (config.submodel) {
        setFanOutEnabled(true)
        setSubmodelSlug(config.submodel.submodel)
        setStartSlug(config.submodel.start)
        setEndSlug(config.submodel.end ?? '')
      } else {
        setFanOutEnabled(false)
      }
      setSuccess('Saved.')
    } catch (e) {
      setError(e instanceof UdmApiError ? e.allMessages.join('; ') : (e instanceof Error ? e.message : 'Save failed'))
    } finally {
      setSaving(false)
    }
  }

  function updateRow(property: string, patch: Partial<Row>) {
    setRows(prev => prev.map(r => (r.property === property ? { ...r, ...patch } : r)))
  }

  if (loading) return <div className={styles.emptyState}>Loading…</div>

  return (
    <div>
      <p style={{ color: '#888', fontSize: '0.85rem', marginTop: 0 }}>
        What each remote property is filled from when this type syncs. Resolved once, at mark_sync
        snapshot time. Leave a value blank to skip that property when syncing — it's ignored, not an
        error.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {rows.map(row => (
          <div key={row.property} style={{ border: '1px solid #e2e8f0', borderRadius: 6, padding: '0.6rem 0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <span style={{ fontFamily: 'monospace', fontWeight: 600, flex: 1 }}>{row.property}</span>
              <Dropdown
                value={row.kind}
                options={KIND_OPTIONS}
                disabled={saving || (fanOutEnabled && (row.property === 'DTSTART' || row.property === 'DTEND'))}
                onChange={e => {
                  const nextRows = rows.map(r => (r.property === row.property ? { ...r, kind: e.value as SourceKind } : r))
                  setRows(nextRows)
                  void save({ rows: nextRows })
                }}
              />
            </div>
            {fanOutEnabled && (row.property === 'DTSTART' || row.property === 'DTEND') ? (
              <div style={{ fontSize: '0.8rem', color: '#888', fontStyle: 'italic' }}>
                Filled per-VEVENT from the submodel fan-out below while fan-out is on — this field's
                own binding is unused.
              </div>
            ) : row.kind === 'template' ? (
              <InputTextarea
                className="p-inputtext-sm"
                style={{ width: '100%' }}
                autoResize
                rows={3}
                value={row.value}
                disabled={saving}
                placeholder="{{ effective.title }} — {{ effective.room }}"
                title={KIND_HELP[row.kind]}
                onChange={e => updateRow(row.property, { value: e.target.value })}
                onBlur={() => save({})}
              />
            ) : (
              <InputText
                className="p-inputtext-sm"
                style={{ width: '100%' }}
                value={row.value}
                disabled={saving}
                placeholder={row.kind === 'field' ? 'field_slug' : 'effective_key'}
                title={KIND_HELP[row.kind]}
                onChange={e => updateRow(row.property, { value: e.target.value })}
                onBlur={() => save({})}
              />
            )}
          </div>
        ))}
      </div>

      <div className={styles.subsectionTitle} style={{ marginTop: '1.25rem' }}>Multiple VEVENTs per entity</div>
      <p style={{ color: '#888', fontSize: '0.8rem', marginTop: '-0.4rem' }}>
        A single effective key, data field, or Jinja template only ever produces <em>one</em> value per
        entity, so bound alone they always push exactly one remote VEVENT. To push several — e.g. one
        per Timeslot — fan out over a submodel_list field instead: SUMMARY/LOCATION/DESCRIPTION above
        stay shared across every VEVENT, but DTSTART/DTEND come from each child's own start/end field,
        one VEVENT per child. Each VEVENT's identity is entity id + child id — stable across edits, so
        moving a slot updates its VEVENT and deleting one removes it, instead of duplicating or
        orphaning remote events.
      </p>
      <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input type="checkbox" checked={fanOutEnabled} disabled={saving}
          onChange={e => {
            const enabled = e.target.checked
            setFanOutEnabled(enabled)
            void save({ fanOutEnabled: enabled })
          }} />
        Fan out over a submodel field
      </label>
      {fanOutEnabled && (
        <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, padding: '0.6rem 0.75rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <div>
              <label style={{ fontSize: '0.7rem', color: '#888' }}>Submodel field slug (e.g. "timeslots")</label>
              <InputText className="p-inputtext-sm" style={{ width: '100%' }} value={submodelSlug} disabled={saving}
                placeholder="timeslots"
                onChange={e => setSubmodelSlug(e.target.value)}
                onBlur={() => save({ submodelSlug })} />
            </div>
            <div>
              <label style={{ fontSize: '0.7rem', color: '#888' }}>Start field slug (on each child)</label>
              <InputText className="p-inputtext-sm" style={{ width: '100%' }} value={startSlug} disabled={saving}
                placeholder="start"
                onChange={e => setStartSlug(e.target.value)}
                onBlur={() => save({ startSlug })} />
            </div>
            <div>
              <label style={{ fontSize: '0.7rem', color: '#888' }}>End field slug (optional — point-in-time slot if left blank)</label>
              <InputText className="p-inputtext-sm" style={{ width: '100%' }} value={endSlug} disabled={saving}
                placeholder="end"
                onChange={e => setEndSlug(e.target.value)}
                onBlur={() => save({ endSlug })} />
            </div>
          </div>
        </div>
      )}

      {error && <div className={styles.error} style={{ marginTop: '0.75rem' }}>{error}</div>}
      {success && <div className={styles.success} style={{ marginTop: '0.75rem' }}>{success}</div>}
    </div>
  )
}
