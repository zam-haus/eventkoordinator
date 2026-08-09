import { useEffect, useState } from 'react'
import { Dropdown } from 'primereact/dropdown'
import { InputText } from 'primereact/inputtext'
import { udmGetTypeEditorTabConfig, udmPutTypeEditorTabConfig, UdmApiError } from '../apiUdm'
import type { TypeEditorTabProps } from './types'
import styles from '../UdmAdminPage.module.css'

type SourceKind = 'effective' | 'field' | 'template'

interface BindingSource {
  effective?: string
  field?: string
  template?: string
}

interface ItemBinding {
  item: string
  variation?: string | null
  price: BindingSource
}

interface PretixConfig {
  bindings?: Record<string, BindingSource>
  parent_event?: BindingSource
  items?: ItemBinding[]
}

interface FieldRow {
  property: string
  kind: SourceKind
  value: string
}

interface ItemRow {
  key: string
  item: string
  variation: string
  priceKind: SourceKind
  priceValue: string
}

const REMOTE_PROPERTIES = ['title', 'start', 'end', 'locale', 'max_participants']

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

const KIND_PLACEHOLDER: Record<SourceKind, string> = {
  effective: 'effective_key',
  field: 'field_slug',
  template: '{{ effective.title }}',
}

function sourceToKindValue(source: BindingSource | null | undefined): { kind: SourceKind, value: string } {
  if (source?.field !== undefined && source.field !== null) return { kind: 'field', value: source.field }
  if (source?.template !== undefined && source.template !== null) return { kind: 'template', value: source.template }
  return { kind: 'effective', value: source?.effective ?? '' }
}

function kindValueToSource(kind: SourceKind, value: string): BindingSource {
  return { [kind]: value }
}

let rowKeySeq = 0
function nextRowKey(): string {
  rowKeySeq += 1
  return `row-${rowKeySeq}`
}

function itemsToRows(items: ItemBinding[]): ItemRow[] {
  return items.map(entry => {
    const price = sourceToKindValue(entry.price)
    return {
      key: nextRowKey(),
      item: entry.item,
      variation: entry.variation ?? '',
      priceKind: price.kind,
      priceValue: price.value,
    }
  })
}

function rowsToItems(rows: ItemRow[]): ItemBinding[] {
  return rows.map(row => ({
    item: row.item,
    variation: row.variation.trim() ? row.variation.trim() : null,
    price: kindValueToSource(row.priceKind, row.priceValue),
  }))
}

/** Dedicated editor for sync_pretix's type-editor tab (events-and-sync.md
 *  §14): subevent field bindings (title/start/end/locale/max_participants,
 *  same shape as the generic BindingsTab), which Pretix **event** a type's
 *  entities create subevents under (dynamic, resolved per-entity — but
 *  pinned at first push so a later change doesn't move an existing
 *  subevent), and which ticket products/variations get price overrides.
 *  sync_pretix's config shape diverges from sync_caldav/sync_ical (it has
 *  parent_event/items on top of bindings), so it gets its own component
 *  instead of sharing BindingsTab.
 *
 *  Parent event and every subevent field binding are always shown, required
 *  for syncing to actually work (except item price overrides, which stay
 *  optional-by-emptiness) — but saving is never blocked: a blank value is
 *  simply ignored at sync time (events-and-sync.md §14) rather than
 *  rejected. Field bindings are a fixed, closed set per plugin (not
 *  admin-defined), so they have no add/remove UI, only the item/variation
 *  table below does (an open-ended list of Pretix products).
 *
 *  Edits save immediately on blur/change (no explicit Save button), like
 *  every other type-editor tab. */
export function PretixBindingsTab({ tabId, configVersionId }: TypeEditorTabProps) {
  const [fieldRows, setFieldRows] = useState<FieldRow[]>([])
  const [parentEventKind, setParentEventKind] = useState<SourceKind>('effective')
  const [parentEventValue, setParentEventValue] = useState('')
  const [itemRows, setItemRows] = useState<ItemRow[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    udmGetTypeEditorTabConfig(configVersionId, tabId)
      .then(res => {
        if (cancelled) return
        const config = res.config as PretixConfig
        const bindings = config.bindings ?? {}
        setFieldRows(REMOTE_PROPERTIES.map(property => ({ property, ...sourceToKindValue(bindings[property]) })))
        const { kind, value } = sourceToKindValue(config.parent_event)
        setParentEventKind(kind)
        setParentEventValue(value)
        setItemRows(itemsToRows(config.items ?? []))
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load tab config') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tabId, configVersionId])

  /** Applies a partial change to local state immediately, then persists the
   *  full resulting config. Saving is never blocked by empty fields —
   *  those are just ignored at sync time. */
  function applyAndPersist(patch: {
    fields?: FieldRow[], parentKind?: SourceKind, parentValue?: string, items?: ItemRow[],
  }) {
    if (patch.fields) setFieldRows(patch.fields)
    if (patch.parentKind !== undefined) setParentEventKind(patch.parentKind)
    if (patch.parentValue !== undefined) setParentEventValue(patch.parentValue)
    if (patch.items) setItemRows(patch.items)

    const fields = patch.fields ?? fieldRows
    const pKind = patch.parentKind ?? parentEventKind
    const pValue = patch.parentValue ?? parentEventValue
    const items = patch.items ?? itemRows
    void persist(fields, pKind, pValue, items)
  }

  async function persist(fields: FieldRow[], pKind: SourceKind, pValue: string, items: ItemRow[]) {
    setError(null)
    setSuccess(null)
    setSaving(true)
    const bindings: Record<string, BindingSource> = {}
    for (const row of fields) bindings[row.property] = kindValueToSource(row.kind, row.value)
    const payload = {
      bindings,
      parent_event: kindValueToSource(pKind, pValue),
      items: rowsToItems(items),
    }
    try {
      const res = await udmPutTypeEditorTabConfig(configVersionId, tabId, payload)
      const config = res.config as PretixConfig
      const savedBindings = config.bindings ?? {}
      setFieldRows(REMOTE_PROPERTIES.map(property => ({ property, ...sourceToKindValue(savedBindings[property]) })))
      const { kind, value } = sourceToKindValue(config.parent_event)
      setParentEventKind(kind)
      setParentEventValue(value)
      setItemRows(itemsToRows(config.items ?? []))
      setSuccess('Saved.')
    } catch (e) {
      setError(e instanceof UdmApiError ? e.allMessages.join('; ') : (e instanceof Error ? e.message : 'Save failed'))
    } finally {
      setSaving(false)
    }
  }

  // ── Field bindings (subevent title/start/end/locale/max_participants) ──

  function updateFieldRow(property: string, patch: Partial<FieldRow>) {
    applyAndPersist({ fields: fieldRows.map(r => (r.property === property ? { ...r, ...patch } : r)) })
  }

  // ── Item / variation bindings ──

  function updateItemRow(key: string, patch: Partial<ItemRow>) {
    applyAndPersist({ items: itemRows.map(r => (r.key === key ? { ...r, ...patch } : r)) })
  }

  function handleRemoveItem(key: string) {
    applyAndPersist({ items: itemRows.filter(r => r.key !== key) })
  }

  function handleAddItem() {
    applyAndPersist({ items: [...itemRows, {
      key: nextRowKey(), item: '', variation: '', priceKind: 'effective', priceValue: '',
    }] })
  }

  if (loading) return <div className={styles.emptyState}>Loading…</div>

  return (
    <div>
      <p style={{ color: '#888', fontSize: '0.85rem', marginTop: 0 }}>
        Configure how this type's entities sync into Pretix: what fills each subevent property,
        which Pretix event new subevents are created under, and which ticket products/variations
        get price overrides. Resolved once per entity, at mark_sync snapshot time — changes here
        take effect for entities marked pending after saving.
      </p>

      {/* ── Field bindings ─────────────────────────────────────────── */}
      <div className={styles.subsectionTitle} style={{ marginTop: '1rem' }}>Subevent fields</div>
      <p style={{ color: '#888', fontSize: '0.8rem', marginTop: '-0.4rem' }}>
        What fills the subevent's title, dates, locale and quota capacity (max_participants). Required
        for syncing to actually create/update a subevent — a value left blank here is simply ignored
        at sync time rather than blocking save.
      </p>
      <p style={{ color: '#888', fontSize: '0.8rem', marginTop: '-0.4rem' }}>
        Unlike CalDAV/iCal sync, Pretix subevents don't fan out into several remote objects — one
        entity always maps to exactly one subevent, which is one continuous span. If this type has
        multiple timeslots, don't bind <code>start</code>/<code>end</code> to a single effective key,
        data field, or template (any of those only ever produce one value); instead compute them in
        the policy as the <em>earliest</em> timeslot start and <em>latest</em> timeslot end, so the one
        subevent covers the full range. Bind <code>start</code>/<code>end</code> to whatever effective
        keys the policy publishes for that min/max.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1.5rem' }}>
        {fieldRows.map(row => (
          <div key={row.property} style={{ border: '1px solid #e2e8f0', borderRadius: 6, padding: '0.6rem 0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <span style={{ fontFamily: 'monospace', fontWeight: 600, flex: 1 }}>{row.property}</span>
              <Dropdown
                value={row.kind}
                options={KIND_OPTIONS}
                disabled={saving}
                onChange={e => updateFieldRow(row.property, { kind: e.value as SourceKind })}
              />
            </div>
            <InputText
              className="p-inputtext-sm"
              style={{ width: '100%' }}
              value={row.value}
              disabled={saving}
              placeholder={KIND_PLACEHOLDER[row.kind]}
              title={KIND_HELP[row.kind]}
              onChange={e => setFieldRows(prev => prev.map(r => (r.property === row.property ? { ...r, value: e.target.value } : r)))}
              onBlur={() => updateFieldRow(row.property, {})}
            />
          </div>
        ))}
      </div>

      {/* ── Parent event ────────────────────────────────────────────── */}
      <div className={styles.subsectionTitle}>Parent event</div>
      <p style={{ color: '#888', fontSize: '0.8rem', marginTop: '-0.4rem' }}>
        Required for syncing. Which Pretix event new subevents are created under, resolved per entity
        (e.g. from a policy effective key, a data field, or a template) — no separate admin screen to
        assign events by hand. A blank value here just means this type's entities aren't synced to
        Pretix yet, it doesn't block saving. Only consulted to create a <em>new</em> subevent — once
        one exists, it keeps using the event it was created under even if this later resolves
        differently for that entity; a mismatch then shows up as a "parent_event" drift entry instead
        of silently moving the subevent.
      </p>
      <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, padding: '0.6rem 0.75rem', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
          <span style={{ flex: 1, fontWeight: 600, fontSize: '0.85rem' }}>Event slug source</span>
          <Dropdown
            value={parentEventKind}
            options={KIND_OPTIONS}
            disabled={saving}
            onChange={e => applyAndPersist({ parentKind: e.value as SourceKind })}
          />
        </div>
        <InputText
          className="p-inputtext-sm"
          style={{ width: '100%' }}
          value={parentEventValue}
          disabled={saving}
          placeholder={parentEventKind === 'template' ? '{{ effective.series_slug }}' : parentEventKind === 'field' ? 'field_slug' : 'event_slug'}
          title={KIND_HELP[parentEventKind]}
          onChange={e => setParentEventValue(e.target.value)}
          onBlur={() => applyAndPersist({ parentValue: parentEventValue })}
        />
      </div>

      {/* ── Item / variation bindings ──────────────────────────────── */}
      <div className={styles.subsectionTitle}>Ticket products &amp; variations</div>
      <p style={{ color: '#888', fontSize: '0.8rem', marginTop: '-0.4rem' }}>
        Every item bound here gets a price override and is automatically included in the subevent's
        shared quota — this list <em>is</em> the quota membership, there's no separate opt-in.
        Quota capacity comes from the max_participants field binding above. Enter either the Pretix
        numeric ID or the item/variation display name — names are matched case-insensitively against
        the live Pretix item list when pushing. Leave variation empty to bind the item itself (its
        default price/variation-less product).
      </p>
      {itemRows.length === 0 && <div className={styles.emptyState}>No item bindings configured yet.</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '0.75rem' }}>
        {itemRows.map(row => (
          <div key={row.key} style={{ border: '1px solid #e2e8f0', borderRadius: 6, padding: '0.6rem 0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 140 }}>
                <label style={{ fontSize: '0.7rem', color: '#888' }}>Item (ID or name)</label>
                <InputText className="p-inputtext-sm" value={row.item} disabled={saving}
                  placeholder="e.g. Regular Ticket, or 12"
                  onChange={e => setItemRows(prev => prev.map(r => (r.key === row.key ? { ...r, item: e.target.value } : r)))}
                  onBlur={() => updateItemRow(row.key, {})} />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 140 }}>
                <label style={{ fontSize: '0.7rem', color: '#888' }}>Variation (ID or name, optional)</label>
                <InputText className="p-inputtext-sm" value={row.variation} disabled={saving}
                  placeholder="e.g. Student"
                  onChange={e => setItemRows(prev => prev.map(r => (r.key === row.key ? { ...r, variation: e.target.value } : r)))}
                  onBlur={() => updateItemRow(row.key, {})} />
              </div>
              <button type="button" className={`${styles.btn} ${styles.btnDanger}`}
                disabled={saving} onClick={() => handleRemoveItem(row.key)} aria-label="Remove item binding">
                Remove
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
              <label style={{ fontSize: '0.7rem', color: '#888' }}>Price override source</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <Dropdown
                  value={row.priceKind}
                  options={KIND_OPTIONS}
                  disabled={saving}
                  onChange={e => updateItemRow(row.key, { priceKind: e.value as SourceKind })}
                />
                <InputText
                  className="p-inputtext-sm"
                  style={{ flex: 1 }}
                  value={row.priceValue}
                  disabled={saving}
                  placeholder={KIND_PLACEHOLDER[row.priceKind]}
                  title={KIND_HELP[row.priceKind]}
                  onChange={e => setItemRows(prev => prev.map(r => (r.key === row.key ? { ...r, priceValue: e.target.value } : r)))}
                  onBlur={() => updateItemRow(row.key, {})}
                />
              </div>
            </div>
          </div>
        ))}
      </div>
      <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
        disabled={saving} onClick={handleAddItem}>
        Add item binding
      </button>

      {error && <div className={styles.error} style={{ marginTop: '0.75rem' }}>{error}</div>}
      {success && <div className={styles.success} style={{ marginTop: '0.75rem' }}>{success}</div>}
    </div>
  )
}
