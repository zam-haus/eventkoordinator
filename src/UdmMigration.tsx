import { useState, useEffect } from 'react'
import {
  udmMigrationPreview,
  udmExecuteMigration,
  udmGetConfigVersion,
  udmListConfigs,
  udmListConfigVersions,
  udmListTypes,
  udmBulkMigrationPreview,
  udmCreateBulkMigration,
  udmExecuteBulkMigration,
  udmGetBulkMigration,
  UdmApiError,
  type EntityOut,
  type ConfigVersionOut,
  type FieldDefinitionOut,
  type MigrationAction,
  type MigrationFieldMappingIn,
  type FieldConfigOut,
  type ConfigVersionListItem,
  type UDMTypeOut,
  type BulkMigrationOut,
} from './apiUdm'

// Per-submodel-field migration state
interface SubmodelSectionState {
  sourceVersionId: string
  targetVersionId: string
  rows: MappingRow[]
  targetFields: FieldDefinitionOut[]
  mapping: MappingState
}
type SubmodelMappingsState = Record<string, SubmodelSectionState>

// Per-workflow-field state mapping
interface WorkflowSectionState {
  fieldSlug: string
  sourceStates: string[]
  targetStates: string[]
  mapping: Record<string, string>
}
type WorkflowMappingsState = Record<string, WorkflowSectionState>

// ── Shared types & helpers ──────────────────────────────────────────────────────

// Source-data-type → target-data-type pairs the backend treats as a safe map.
// Mirrors the _ALLOWED set in api.py::migration_preview.
const COMPATIBLE = new Set([
  'integer→float', 'text_short→text_long', 'text_long→text_markdown',
  'select_single→select_multi', 'user_select→user_select_multi',
  'group_select→group_select_multi', 'entity_select→entity_select_multi',
])

function isCompatible(src: string, tgt: string): boolean {
  return src === tgt || COMPATIBLE.has(`${src}→${tgt}`)
}

interface MappingRow {
  sourceSlug: string
  sourceDataType: string
  conflictReason?: string | null
}

// Per-source-slug choice. target is '' unless action === 'map'.
type Mapping = { action: MigrationAction; target: string }
type MappingState = Record<string, Mapping>

/** Build an auto-suggested mapping for client-side flows (bulk migration). */
function suggestMappings(sourceFields: FieldDefinitionOut[], targetFields: FieldDefinitionOut[]): MappingState {
  const tgtBySlug = new Map(targetFields.map(f => [f.slug, f]))
  const state: MappingState = {}
  for (const sf of sourceFields) {
    const tgt = tgtBySlug.get(sf.slug)
    if (tgt && isCompatible(sf.data_type, tgt.data_type)) {
      state[sf.slug] = { action: 'map', target: sf.slug }
    } else {
      state[sf.slug] = { action: 'overflow', target: '' }
    }
  }
  return state
}

function toPayload(state: MappingState): MigrationFieldMappingIn[] {
  return Object.entries(state).map(([sourceSlug, m]) => ({
    source_field_slug: sourceSlug,
    action: m.action,
    target_field_slug: m.action === 'map' ? (m.target || null) : null,
  }))
}

/** True if every "map" row has a target chosen (required by the backend). */
function mappingsValid(state: MappingState): boolean {
  return Object.values(state).every(m => m.action !== 'map' || !!m.target)
}

// ── Shared field-mapping table ──────────────────────────────────────────────────

interface FieldMappingTableProps {
  rows: MappingRow[]
  targetFields: FieldDefinitionOut[]
  value: MappingState
  onChange: (next: MappingState) => void
  disabled?: boolean
}

const th: React.CSSProperties = { textAlign: 'left', padding: '0.4rem 0.6rem', fontSize: '0.8rem', color: '#555', borderBottom: '1px solid #e0e0e0' }
const td: React.CSSProperties = { padding: '0.35rem 0.6rem', fontSize: '0.85rem', borderBottom: '1px solid #f0f0f0', verticalAlign: 'top' }
const ctrl: React.CSSProperties = { padding: '0.25rem 0.45rem', border: '1px solid #ccc', borderRadius: '4px', fontSize: '0.82rem', background: '#fff' }

function FieldMappingTable({ rows, targetFields, value, onChange, disabled }: FieldMappingTableProps) {
  function set(slug: string, patch: Partial<Mapping>) {
    onChange({ ...value, [slug]: { ...value[slug], ...patch } })
  }

  if (rows.length === 0) {
    return <div style={{ color: '#888', fontStyle: 'italic', fontSize: '0.85rem' }}>Source version has no fields to map.</div>
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr>
          <th style={th}>Source field</th>
          <th style={th}>Action</th>
          <th style={th}>Target field</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(row => {
          const m = value[row.sourceSlug] ?? { action: 'overflow' as MigrationAction, target: '' }
          return (
            <tr key={row.sourceSlug}>
              <td style={td}>
                <div style={{ fontWeight: 600 }}>{row.sourceSlug}</div>
                <div style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#999' }}>{row.sourceDataType}</div>
                {row.conflictReason && (
                  <div style={{ color: '#b45309', fontSize: '0.75rem', marginTop: '0.15rem' }}>⚠ {row.conflictReason}</div>
                )}
              </td>
              <td style={td}>
                <select style={ctrl} value={m.action} disabled={disabled}
                  onChange={e => {
                    const action = e.target.value as MigrationAction
                    set(row.sourceSlug, { action, target: action === 'map' ? m.target : '' })
                  }}>
                  <option value="map">Map to field</option>
                  <option value="overflow">Keep in overflow</option>
                  <option value="discard">Discard</option>
                </select>
              </td>
              <td style={td}>
                {m.action === 'map' ? (
                  <select style={{ ...ctrl, borderColor: m.target ? '#ccc' : '#dc2626' }} value={m.target} disabled={disabled}
                    onChange={e => set(row.sourceSlug, { target: e.target.value })}>
                    <option value="">— choose —</option>
                    {targetFields.map(tf => {
                      const compat = isCompatible(row.sourceDataType, tf.data_type)
                      return (
                        <option key={tf.slug} value={tf.slug}>
                          {tf.slug} ({tf.data_type}){compat ? '' : ' — incompatible'}
                        </option>
                      )
                    })}
                  </select>
                ) : (
                  <span style={{ color: '#aaa', fontSize: '0.8rem' }}>—</span>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Per-entity migration assistant ──────────────────────────────────────────────

interface MigrationAssistantProps {
  entityId: string
  targetTypeId: string | null
  sourceConfig: ConfigVersionOut | null
  onMigrated: (updated: EntityOut) => void
}

const panel: React.CSSProperties = {
  border: '1px solid #f59e0b', background: '#fffbeb', borderRadius: '8px',
  padding: '1rem', marginBottom: '1rem',
}
const primaryBtn: React.CSSProperties = {
  padding: '0.45rem 1rem', background: '#d97706', color: '#fff', border: 'none',
  borderRadius: '4px', cursor: 'pointer', fontSize: '0.9rem',
}
const secondaryBtn: React.CSSProperties = {
  padding: '0.45rem 1rem', background: '#fff', color: '#555', border: '1px solid #ccc',
  borderRadius: '4px', cursor: 'pointer', fontSize: '0.9rem',
}

export function MigrationAssistant({ entityId, targetTypeId, sourceConfig, onMigrated }: MigrationAssistantProps) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [migrationId, setMigrationId] = useState<string | null>(null)
  const [rows, setRows] = useState<MappingRow[]>([])
  const [targetFields, setTargetFields] = useState<FieldDefinitionOut[]>([])
  const [mapping, setMapping] = useState<MappingState>({})

  async function startPreview() {
    if (!targetTypeId) { setErrors(['This entity has no UDM type, so it cannot be migrated automatically.']); return }
    setOpen(true)
    setLoading(true)
    setErrors([])
    try {
      const preview = await udmMigrationPreview(entityId, { targetTypeId })
      setMigrationId(preview.migration_id)
      // Seed the mapping table from the server's suggestions.
      const state: MappingState = {}
      const builtRows: MappingRow[] = preview.field_previews.map(fp => {
        state[fp.source_slug] = {
          action: fp.suggested_action,
          target: fp.suggested_action === 'map' ? (fp.suggested_target_slug ?? '') : '',
        }
        return { sourceSlug: fp.source_slug, sourceDataType: fp.source_data_type, conflictReason: fp.conflict_reason }
      })
      setMapping(state)
      setRows(builtRows)
      // Fetch the target version's fields to populate the target dropdowns.
      const tgt = await udmGetConfigVersion(preview.target_version_id)
      setTargetFields(tgt.fields)
    } catch (e) {
      setErrors(e instanceof UdmApiError ? e.allMessages : [e instanceof Error ? e.message : 'Failed to build migration preview'])
    } finally {
      setLoading(false)
    }
  }

  async function confirm() {
    if (!migrationId) return
    setBusy(true)
    setErrors([])
    try {
      const updated = await udmExecuteMigration(entityId, migrationId, toPayload(mapping))
      setOpen(false)
      onMigrated(updated)
    } catch (e) {
      setErrors(e instanceof UdmApiError ? e.allMessages : [e instanceof Error ? e.message : 'Migration failed'])
    } finally {
      setBusy(false)
    }
  }

  const sourceVersionShort = sourceConfig ? sourceConfig.version_id.slice(0, 8) : ''

  return (
    <div style={panel}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem' }}>
        <div>
          <strong style={{ color: '#92400e' }}>Outdated config version</strong>
          <div style={{ fontSize: '0.85rem', color: '#92400e', marginTop: '0.2rem' }}>
            This entity is pinned to an archived config version{sourceVersionShort && ` (${sourceVersionShort}…)`} and is read-only.
            Migrate it to the current version to resume editing.
          </div>
        </div>
        {!open && (
          <button type="button" style={primaryBtn} onClick={() => void startPreview()}>
            Migrate…
          </button>
        )}
      </div>

      {errors.length > 0 && (
        <div style={{ color: '#dc2626', fontSize: '0.85rem', marginTop: '0.6rem' }}>
          {errors.map((m, i) => <div key={i}>{m}</div>)}
        </div>
      )}

      {open && (
        <div style={{ marginTop: '0.8rem', background: '#fff', border: '1px solid #fde68a', borderRadius: '6px', padding: '0.75rem' }}>
          {loading ? (
            <div style={{ fontSize: '0.85rem', color: '#888' }}>Building preview…</div>
          ) : (
            <>
              <div style={{ fontSize: '0.85rem', color: '#555', marginBottom: '0.5rem' }}>
                Choose how each field maps into the new version. Fields kept in overflow are preserved as raw text; discarded fields are dropped.
              </div>
              <FieldMappingTable rows={rows} targetFields={targetFields} value={mapping} onChange={setMapping} disabled={busy} />
              <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.8rem', justifyContent: 'flex-end' }}>
                <button type="button" style={secondaryBtn} onClick={() => setOpen(false)} disabled={busy}>Cancel</button>
                <button type="button" style={primaryBtn} onClick={() => void confirm()} disabled={busy || !mappingsValid(mapping)}>
                  {busy ? 'Migrating…' : 'Confirm migration'}
                </button>
              </div>
              {!mappingsValid(mapping) && (
                <div style={{ color: '#dc2626', fontSize: '0.78rem', marginTop: '0.4rem', textAlign: 'right' }}>
                  Every "Map to field" row needs a target field.
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Workflow state mapping table ────────────────────────────────────────────────

interface WorkflowStateMappingTableProps {
  section: WorkflowSectionState
  onChange: (next: WorkflowSectionState) => void
  disabled?: boolean
}

function WorkflowStateMappingTable({ section, onChange, disabled }: WorkflowStateMappingTableProps) {
  const { sourceStates, targetStates, mapping } = section

  function setStateMapping(fromState: string, toState: string) {
    onChange({ ...section, mapping: { ...mapping, [fromState]: toState } })
  }

  if (sourceStates.length === 0) {
    return <div style={{ color: '#888', fontStyle: 'italic', fontSize: '0.85rem' }}>Source workflow has no states.</div>
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr>
          <th style={th}>Source state</th>
          <th style={th}>Maps to</th>
        </tr>
      </thead>
      <tbody>
        {sourceStates.map(fromState => {
          const toState = mapping[fromState] ?? ''
          return (
            <tr key={fromState}>
              <td style={td}>
                <span style={{ fontWeight: 600, fontFamily: 'monospace' }}>{fromState}</span>
              </td>
              <td style={td}>
                <select style={{ ...ctrl, borderColor: toState ? '#ccc' : '#f59e0b' }} value={toState} disabled={disabled}
                  onChange={e => setStateMapping(fromState, e.target.value)}>
                  <option value="">— keep as-is (same name) —</option>
                  {targetStates.map(ts => (
                    <option key={ts} value={ts}>{ts}</option>
                  ))}
                </select>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Bulk migration (admin) ──────────────────────────────────────────────────────

const adminBtn: React.CSSProperties = {
  padding: '0.45rem 1rem', background: '#0066cc', color: '#fff', border: 'none',
  borderRadius: '4px', cursor: 'pointer', fontSize: '0.9rem',
}
const sel: React.CSSProperties = {
  padding: '0.4rem 0.6rem', border: '1px solid #ccc', borderRadius: '4px',
  fontSize: '0.88rem', background: '#fff', minWidth: '240px',
}
const fieldLabel: React.CSSProperties = { fontSize: '0.8rem', color: '#666', display: 'block', marginBottom: '0.2rem' }

function versionLabel(v: ConfigVersionListItem): string {
  const pub = v.published_at
    ? new Date(v.published_at).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
    : '—'
  return `${v.id.slice(0, 8)}… · ${v.status} · published ${pub}`
}

function sourceVersionLabel(v: ConfigVersionListItem): string {
  return `${versionLabel(v)} · ${v.entity_count} entit${v.entity_count === 1 ? 'y' : 'ies'}`
}

function configLabel(c: FieldConfigOut): string {
  const pub = c.last_published_at
    ? new Date(c.last_published_at).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
    : 'never'
  return `${c.name} · ${c.stale_entity_count} stale / ${c.entity_count} total · published ${pub}`
}

export function BulkMigrationTab() {
  const [configs, setConfigs] = useState<FieldConfigOut[]>([])
  const [types, setTypes] = useState<UDMTypeOut[]>([])

  // Source config + versions
  const [configId, setConfigId] = useState('')
  const [versions, setVersions] = useState<ConfigVersionListItem[]>([])
  const [sourceId, setSourceId] = useState('')

  // Target config + versions (defaults to same as source)
  const [targetConfigId, setTargetConfigId] = useState('')
  const [targetVersions, setTargetVersions] = useState<ConfigVersionListItem[]>([])
  const [targetId, setTargetId] = useState('')

  const [typeFilterId, setTypeFilterId] = useState('')

  const [rows, setRows] = useState<MappingRow[]>([])
  const [targetFields, setTargetFields] = useState<FieldDefinitionOut[]>([])
  const [mapping, setMapping] = useState<MappingState>({})
  const [affected, setAffected] = useState<number | null>(null)

  const [submodelMappings, setSubmodelMappings] = useState<SubmodelMappingsState>({})
  const [workflowMappings, setWorkflowMappings] = useState<WorkflowMappingsState>({})

  const [errors, setErrors] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [plan, setPlan] = useState<BulkMigrationOut | null>(null)
  const [polling, setPolling] = useState(false)

  // Initial load of configs + types
  useEffect(() => {
    void udmListConfigs().then(setConfigs).catch(() => {})
    void udmListTypes().then(setTypes).catch(() => {})
  }, [])

  async function onConfigChange(id: string) {
    setConfigId(id)
    setSourceId(''); setVersions([])
    setTargetConfigId(id); setTargetId(''); setTargetVersions([])
    setRows([]); setMapping({}); setAffected(null); setPlan(null)
    setSubmodelMappings({}); setWorkflowMappings({})
    if (!id) return
    try {
      const vs = await udmListConfigVersions(id)
      setVersions(vs)
      setTargetVersions(vs)
    } catch (e) {
      setErrors([e instanceof Error ? e.message : 'Failed to list versions'])
    }
  }

  async function onTargetConfigChange(id: string) {
    setTargetConfigId(id)
    setTargetId(''); setTargetVersions([])
    setRows([]); setMapping({}); setAffected(null); setPlan(null)
    setSubmodelMappings({}); setWorkflowMappings({})
    if (!id) return
    if (id === configId) {
      setTargetVersions(versions)
      return
    }
    try {
      setTargetVersions(await udmListConfigVersions(id))
    } catch (e) {
      setErrors([e instanceof Error ? e.message : 'Failed to list target versions'])
    }
  }

  async function buildPreview() {
    if (!sourceId || !targetId) { setErrors(['Select both a source and a target version.']); return }
    if (sourceId === targetId) { setErrors(['Source and target versions must differ.']); return }
    setLoading(true)
    setErrors([])
    setPlan(null)
    setSubmodelMappings({}); setWorkflowMappings({})
    try {
      const [src, tgt, prev] = await Promise.all([
        udmGetConfigVersion(sourceId),
        udmGetConfigVersion(targetId),
        udmBulkMigrationPreview(sourceId, targetId, typeFilterId || undefined),
      ])
      const suggested = suggestMappings(src.fields, tgt.fields)
      const tgtBySlug = new Map(tgt.fields.map(f => [f.slug, f]))
      setRows(src.fields.map(sf => {
        const t = tgtBySlug.get(sf.slug)
        const conflict = t && !isCompatible(sf.data_type, t.data_type)
          ? `Incompatible: ${sf.data_type} → ${t.data_type}` : null
        return { sourceSlug: sf.slug, sourceDataType: sf.data_type, conflictReason: conflict }
      }))
      setTargetFields(tgt.fields)
      setMapping(suggested)
      setAffected(prev.affected_entity_count)

      // Build submodel mapping sections: only when submodel config version changed
      const newSubmodelMappings: SubmodelMappingsState = {}
      for (const sf of src.fields) {
        const tf = tgtBySlug.get(sf.slug)
        if (!tf) continue
        if ((sf.data_type === 'submodel_select' || sf.data_type === 'submodel_list') &&
            sf.submodel_config && tf.submodel_config &&
            sf.submodel_config.version_id !== tf.submodel_config.version_id) {
          const subSuggested = suggestMappings(sf.submodel_config.fields, tf.submodel_config.fields)
          const subTgtBySlug = new Map(tf.submodel_config.fields.map(f => [f.slug, f]))
          newSubmodelMappings[sf.slug] = {
            sourceVersionId: sf.submodel_config.version_id,
            targetVersionId: tf.submodel_config.version_id,
            rows: sf.submodel_config.fields.map(ssf => {
              const t2 = subTgtBySlug.get(ssf.slug)
              const conflict = t2 && !isCompatible(ssf.data_type, t2.data_type)
                ? `Incompatible: ${ssf.data_type} → ${t2.data_type}` : null
              return { sourceSlug: ssf.slug, sourceDataType: ssf.data_type, conflictReason: conflict }
            }),
            targetFields: tf.submodel_config.fields,
            mapping: subSuggested,
          }
        }
      }
      setSubmodelMappings(newSubmodelMappings)

      // Build workflow state mapping sections: always when a workflow field is mapped
      const newWorkflowMappings: WorkflowMappingsState = {}
      for (const sf of src.fields) {
        const tf = tgtBySlug.get(sf.slug)
        if (!tf) continue
        if (sf.data_type === 'workflow' && tf.data_type === 'workflow' &&
            (sf as Record<string, unknown>)['workflow_version'] && (tf as Record<string, unknown>)['workflow_version']) {
          const sourceStates = ((sf as Record<string, unknown>)['workflow_version'] as { states: Array<{ name: string }> }).states.map(s => s.name)
          const targetStates = ((tf as Record<string, unknown>)['workflow_version'] as { states: Array<{ name: string }> }).states.map(s => s.name)
          const targetStateSet = new Set(targetStates)
          const stateMapping: Record<string, string> = {}
          for (const s of sourceStates) {
            stateMapping[s] = targetStateSet.has(s) ? s : ''
          }
          newWorkflowMappings[sf.slug] = {
            fieldSlug: sf.slug,
            sourceStates,
            targetStates,
            mapping: stateMapping,
          }
        }
      }
      setWorkflowMappings(newWorkflowMappings)
    } catch (e) {
      setErrors(e instanceof UdmApiError ? e.allMessages : [e instanceof Error ? e.message : 'Preview failed'])
    } finally {
      setLoading(false)
    }
  }

  async function pollPlan(planId: string) {
    setPolling(true)
    const tick = async () => {
      try {
        const p = await udmGetBulkMigration(planId)
        setPlan(p)
        if (p.status === 'running' || p.status === 'draft') {
          setTimeout(() => void tick(), 1500)
        } else {
          setPolling(false)
        }
      } catch {
        setPolling(false)
      }
    }
    await tick()
  }

  async function createAndRun() {
    if (!mappingsValid(mapping)) { setErrors(['Every "Map to field" row needs a target field.']); return }
    const invalidSubmodel = Object.entries(submodelMappings)
      .filter(([slug]) => mapping[slug]?.action === 'map')
      .find(([, sec]) => !mappingsValid(sec.mapping))
    if (invalidSubmodel) {
      setErrors([`Every "Map to field" row in submodel field "${invalidSubmodel[0]}" needs a target field.`])
      return
    }
    setLoading(true)
    setErrors([])
    try {
      const submodelPayload = Object.entries(submodelMappings)
        .filter(([slug]) => mapping[slug]?.action === 'map')
        .map(([slug, sec]) => ({
          source_parent_field_slug: slug,
          target_submodel_version_id: sec.targetVersionId,
          field_mappings: toPayload(sec.mapping),
        }))

      const workflowPayload = Object.entries(workflowMappings)
        .filter(([slug]) => mapping[slug]?.action === 'map')
        .map(([, sec]) => ({
          field_slug: sec.fieldSlug,
          state_mappings: Object.entries(sec.mapping)
            .filter(([, toState]) => toState !== '')
            .map(([fromState, toState]) => ({ from_state: fromState, to_state: toState })),
        }))
        .filter(wm => wm.state_mappings.length > 0)

      const created = await udmCreateBulkMigration({
        source_version_id: sourceId,
        target_version_id: targetId,
        user_defined_model_type_filter_id: typeFilterId || null,
        field_mappings: toPayload(mapping),
        submodel_mappings: submodelPayload,
        workflow_state_mappings: workflowPayload,
      })
      setPlan(created)
      await udmExecuteBulkMigration(created.id)
      void pollPlan(created.id)
    } catch (e) {
      setErrors(e instanceof UdmApiError ? e.allMessages : [e instanceof Error ? e.message : 'Failed to start migration'])
    } finally {
      setLoading(false)
    }
  }

  const section: React.CSSProperties = { background: '#fff', border: '1px solid #e0e0e0', borderRadius: '8px', padding: '1.25rem', marginBottom: '1rem' }
  const colHead: React.CSSProperties = { fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#888', marginBottom: '0.6rem' }
  const sourceCandidates = versions.filter(v => v.entity_count > 0)
  const targetPublished = targetVersions.filter(v => v.status === 'published')
  const crossConfig = !!targetConfigId && targetConfigId !== configId

  return (
    <div>
      <div style={section}>
        <div style={{ fontWeight: 600, marginBottom: '1rem' }}>Bulk migrate entities between config versions</div>

        {/* FROM / TO two-column grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: '0 1rem', alignItems: 'start' }}>
          {/* FROM column */}
          <div>
            <div style={colHead}>From</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
              <div>
                <label style={fieldLabel}>Field config</label>
                <select style={{ ...sel, width: '100%' }} value={configId} onChange={e => void onConfigChange(e.target.value)}>
                  <option value="">— select config —</option>
                  {configs.map(c => <option key={c.id} value={c.id}>{configLabel(c)}</option>)}
                </select>
              </div>
              {configId && (
                <div>
                  <label style={fieldLabel}>Version</label>
                  <select style={{ ...sel, width: '100%' }} value={sourceId} onChange={e => setSourceId(e.target.value)}>
                    <option value="">— select version —</option>
                    {(sourceCandidates.length ? sourceCandidates : versions).map(v => (
                      <option key={v.id} value={v.id}>{sourceVersionLabel(v)}</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          </div>

          {/* Arrow separator */}
          <div style={{ display: 'flex', alignItems: 'center', paddingTop: '1.6rem', color: '#aaa', fontSize: '1.4rem', userSelect: 'none' }}>→</div>

          {/* TO column */}
          <div>
            <div style={colHead}>To</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
              <div>
                <label style={fieldLabel}>Field config</label>
                <select style={{ ...sel, width: '100%' }} value={targetConfigId} onChange={e => void onTargetConfigChange(e.target.value)} disabled={!configId}>
                  <option value="">— select config —</option>
                  {configs.map(c => <option key={c.id} value={c.id}>{configLabel(c)}</option>)}
                </select>
              </div>
              {targetConfigId && (
                <div>
                  <label style={fieldLabel}>Version</label>
                  <select style={{ ...sel, width: '100%' }} value={targetId} onChange={e => setTargetId(e.target.value)}>
                    <option value="">— select version —</option>
                    {(targetPublished.length ? targetPublished : targetVersions).map(v => (
                      <option key={v.id} value={v.id}>{versionLabel(v)}</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* UDM type filter — full width below the grid */}
        {configId && (
          <div style={{ marginTop: '0.9rem', paddingTop: '0.9rem', borderTop: '1px solid #f0f0f0' }}>
            <label style={fieldLabel}>
              Limit to UDM type
              <span style={{ color: crossConfig ? '#92400e' : '#aaa', marginLeft: '0.4rem' }}>
                {crossConfig ? '(recommended for cross-config)' : '(optional)'}
              </span>
            </label>
            <select style={{ ...sel, borderColor: crossConfig && !typeFilterId ? '#f59e0b' : undefined }} value={typeFilterId} onChange={e => setTypeFilterId(e.target.value)}>
              <option value="">— all types —</option>
              {types.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
        )}

        <div style={{ marginTop: '0.9rem' }}>
          <button type="button" style={adminBtn} onClick={() => void buildPreview()} disabled={loading || !sourceId || !targetId}>
            {loading ? 'Working…' : 'Preview mapping'}
          </button>
        </div>

        {errors.length > 0 && (
          <div style={{ color: '#dc2626', fontSize: '0.85rem', marginTop: '0.6rem' }}>
            {errors.map((m, i) => <div key={i}>{m}</div>)}
          </div>
        )}
      </div>

      {affected !== null && (
        <>
          <div style={section}>
            <div style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
              Field mapping
              <span style={{ fontWeight: 400, color: '#666', marginLeft: '0.5rem' }}>
                {affected} entit{affected === 1 ? 'y' : 'ies'} will be migrated
              </span>
            </div>
            <FieldMappingTable rows={rows} targetFields={targetFields} value={mapping} onChange={setMapping} disabled={polling} />
          </div>

          {Object.entries(submodelMappings)
            .filter(([slug]) => mapping[slug]?.action === 'map')
            .map(([slug, sec]) => (
              <div key={slug} style={section}>
                <div style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
                  Submodel field mapping: <span style={{ fontFamily: 'monospace' }}>{slug}</span>
                  <span style={{ fontWeight: 400, color: '#666', marginLeft: '0.5rem', fontSize: '0.85rem' }}>
                    submodel config version changed — map child node fields
                  </span>
                </div>
                <FieldMappingTable
                  rows={sec.rows}
                  targetFields={sec.targetFields}
                  value={sec.mapping}
                  onChange={nextMapping => setSubmodelMappings(prev => ({ ...prev, [slug]: { ...prev[slug], mapping: nextMapping } }))}
                  disabled={polling}
                />
              </div>
            ))}

          {Object.entries(workflowMappings)
            .filter(([slug]) => mapping[slug]?.action === 'map')
            .map(([slug, sec]) => (
              <div key={slug} style={section}>
                <div style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
                  Workflow state mapping: <span style={{ fontFamily: 'monospace' }}>{slug}</span>
                  <span style={{ fontWeight: 400, color: '#666', marginLeft: '0.5rem', fontSize: '0.85rem' }}>
                    override how entity states are remapped; leave "keep as-is" for same-named states
                  </span>
                </div>
                <WorkflowStateMappingTable
                  section={sec}
                  onChange={nextSec => setWorkflowMappings(prev => ({ ...prev, [slug]: nextSec }))}
                  disabled={polling}
                />
              </div>
            ))}

          <div style={section}>
            <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
              <button type="button" style={{ ...adminBtn, background: '#16a34a' }}
                onClick={() => void createAndRun()}
                disabled={loading || polling || affected === 0 || !mappingsValid(mapping)}>
                {polling ? 'Running…' : `Migrate ${affected} entit${affected === 1 ? 'y' : 'ies'}`}
              </button>
              {plan && (
                <span style={{ fontSize: '0.85rem', color: '#555' }}>
                  Status: <strong>{plan.status}</strong> · {plan.done_entities} done · {plan.failed_entities} failed
                  {plan.total_entities ? ` / ${plan.total_entities}` : ''}
                </span>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
