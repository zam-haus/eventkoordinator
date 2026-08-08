import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { TreeTable } from 'primereact/treetable'
import { Column } from 'primereact/column'
import { MultiSelect } from 'primereact/multiselect'
import { InputText } from 'primereact/inputtext'
import { Button } from 'primereact/button'
import { ToggleButton } from 'primereact/togglebutton'
import { OverlayPanel } from 'primereact/overlaypanel'
import type { TreeNode } from 'primereact/treenode'
import {
  udmListTypes,
  udmGetTypeConfig,
  udmListEntitiesByType,
  udmPatchEntity,
  udmGetEntity,
  udmValidationPreview,
  UdmApiError,
  type UDMTypeOut,
  type ConfigVersionOut,
  type EntityOut,
  type DashboardColumnOut,
  type PolicyMessage,
} from './apiUdm'
import { getLang, FieldPreview, fieldPreviewText, FieldInput, PolicyMessageList } from './udm-editors'
import { FieldCommitWrapper, BLUR_COMMIT_TYPES, LARGE_TYPES } from './udm-editors/FieldCommitWrapper'

// ── Constants ──────────────────────────────────────────────────────────────────

const SKIP_DATA_TYPES = new Set([
  'tab_container', 'tab', 'save_button', 'hstack', 'hstack_group', 'tab_prev', 'tab_next',
])

// Editing happens in a popup rather than inline in the cell, so even
// markdown/multiline/richtext/image/file fields (LARGE_TYPES) work fine here.
// Submodels are still excluded — nested editing belongs in the full entity
// editor, not the dashboard grid.
function isUneditableInGrid(dataType: string): boolean {
  return dataType.startsWith('submodel')
}

function isForField(msg: PolicyMessage, slug: string): boolean {
  return (msg.highlight_fields ?? []).some(p => p.split('.')[0] === slug)
}

/** Messages from a failed save, narrowed to the one field being edited.
 *  Unrelated form-wide messages (other fields, form status, etc.) are
 *  dropped — only a totally unstructured failure (no pydantic/field/policy
 *  detail at all) falls back to the bare error text, since there's nothing
 *  else to attribute it to. */
function errorToFieldMessages(e: unknown, slug: string): PolicyMessage[] {
  if (e instanceof UdmApiError) {
    const fieldSpecific: PolicyMessage[] = []
    for (const pe of e.pydanticErrors) {
      const loc = pe.loc.filter(s => s !== 'body' && s !== 'payload')
      if (loc[0] === slug) fieldSpecific.push({ level: 'error', text: loc.length > 1 ? `${loc.slice(1).join(' → ')}: ${pe.msg}` : pe.msg })
    }
    for (const err of e.fieldErrors[slug] ?? []) fieldSpecific.push({ level: 'error', text: err })
    for (const pm of e.policyMessages) if (isForField(pm, slug)) fieldSpecific.push(pm)
    if (fieldSpecific.length > 0) return fieldSpecific
    if (e.pydanticErrors.length === 0 && Object.keys(e.fieldErrors).length === 0 && e.policyMessages.length === 0) {
      return [{ level: 'error', text: e.message }]
    }
    // The save failed, but every message belongs to some other field —
    // still say so here (it did not save), without dumping the unrelated list.
    return [{ level: 'error', text: 'Not saved — see the full form for details.' }]
  }
  return [{ level: 'error', text: e instanceof Error ? e.message : 'Save failed' }]
}

const FILTER_HELP = [
  'Lucene-like filter query:',
  '  term            berlin          (any field, submodels included)',
  '  field           city:Berlin',
  '  phrase          title:"hello world"',
  '  wildcards       name:an*   name:B?rlin',
  '  ranges          age:[18 TO 30]   age:{18 TO 30}   age:[18 TO *]',
  '  booleans        a AND b, a OR b, NOT a, +a -b, (a OR b) AND c',
  '  submodels       any(participants: status:confirmed)',
  '                  all(participants: status:confirmed)',
  '                  none(participants: status:rejected)',
  '                  participants.name:anna   (shorthand for any)',
  'Not supported yet: fuzzy (a~2), proximity ("a b"~3), boosting (a^2).',
].join('\n')

const DASH_PREFIX = '__dash__:'
function dashId(key: string) { return `${DASH_PREFIX}${key}` }

// ── Row types ──────────────────────────────────────────────────────────────────

type TypeRow = { kind: 'type'; type: UDMTypeOut; loading: boolean }
type EntityRow = { kind: 'entity'; entity: EntityOut; config: ConfigVersionOut | null }
type RowData = TypeRow | EntityRow

// ── Column descriptors ─────────────────────────────────────────────────────────

interface FieldColOpt { kind: 'field'; id: string; label: string; slug: string }
interface DashColOpt  { kind: 'dash';  id: string; label: string; key: string; renderer: string }
type ColOpt = FieldColOpt | DashColOpt

// ── Renderers ──────────────────────────────────────────────────────────────────

interface ProgressBarValue { current: number; max: number; color?: string }

function ProgressBarCell({ value }: { value: unknown }) {
  const v = value as ProgressBarValue | null
  if (!v || typeof v !== 'object' || !v.max) return <span style={{ color: '#9ca3af' }}>—</span>
  const pct = Math.min((v.current / v.max) * 100, 100)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
      <div style={{ flex: 1, height: '8px', background: '#e5e7eb', borderRadius: '4px', minWidth: '60px' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: v.color ?? '#3b82f6', borderRadius: '4px' }} />
      </div>
      <span style={{ fontSize: '0.78rem', color: '#6b7280', whiteSpace: 'nowrap' }}>
        {v.current}/{v.max}
      </span>
    </div>
  )
}

interface MeterSegment { label: string; value: number; color: string }

function MeterCell({ value }: { value: unknown }) {
  if (!Array.isArray(value) || value.length === 0) return <span style={{ color: '#9ca3af' }}>—</span>
  const segs = value as MeterSegment[]
  const total = segs.reduce((s, v) => s + (v.value ?? 0), 0)
  if (!total) return <span style={{ color: '#9ca3af' }}>—</span>
  return (
    <div style={{ display: 'flex', height: '8px', borderRadius: '4px', overflow: 'hidden', minWidth: '80px' }}>
      {segs.filter(s => s.value > 0).map((s, i) => (
        <div
          key={i}
          style={{ width: `${(s.value / total) * 100}%`, background: s.color ?? '#9ca3af' }}
          title={`${s.label}: ${s.value}`}
        />
      ))}
    </div>
  )
}

function DashboardCell({ col }: { col: DashboardColumnOut; entity: EntityOut }) {
  switch (col.renderer) {
    case 'progress_bar': return <ProgressBarCell value={col.value} />
    case 'meter':        return <MeterCell value={col.value} />
    default:             return <span style={{ fontSize: '0.85rem' }}>{String(col.value ?? '')}</span>
  }
}

interface DashboardFieldCellProps {
  fd: ConfigVersionOut['fields'][number]
  entity: EntityOut
  uiLang: string
  editable: boolean
  saving: boolean
  errors: PolicyMessage[]
  onSave: (value: unknown) => Promise<void>
  onEntityRefresh: () => void
  onClearErrors: () => void
}

function DashboardFieldCell({ fd, entity, uiLang, editable, saving, errors, onSave, onEntityRefresh, onClearErrors }: DashboardFieldCellProps) {
  const savedValue = getFieldVal(entity, fd.slug, uiLang)
  const [value, setValue] = useState(savedValue)
  const [dirty, setDirty] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [previewMessages, setPreviewMessages] = useState<PolicyMessage[]>([])
  const opRef = useRef<OverlayPanel>(null)
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Adjust local state during render when the saved value changes underneath
  // us (e.g. a transition refreshed this entity) — avoids the extra render
  // an effect-based sync would cause.
  const [prevSavedValue, setPrevSavedValue] = useState(savedValue)
  if (savedValue !== prevSavedValue) {
    setPrevSavedValue(savedValue)
    setValue(savedValue)
    setDirty(false)
    setPreviewMessages([])
  }

  function closeAndBlur() {
    (document.activeElement as HTMLElement | null)?.blur()
    opRef.current?.hide()
  }

  // While the value is being edited (dirty), query the same
  // validation-preview endpoint UdmEntityEditor uses, so problems surface
  // before the user saves — not just after. Debounced like there (600ms).
  useEffect(() => {
    if (!dirty) return
    if (previewTimer.current) clearTimeout(previewTimer.current)
    previewTimer.current = setTimeout(() => {
      udmValidationPreview(entity.id, { [fd.slug]: value })
        .then(preview => setPreviewMessages((preview.messages ?? []).filter(m => isForField(m, fd.slug))))
        .catch(() => { /* best-effort — ignore lock conflicts and network errors */ })
    }, 600)
    return () => { if (previewTimer.current) clearTimeout(previewTimer.current) }
  }, [dirty, value, entity.id, fd.slug])

  async function commit() {
    // Nothing changed — some field types (e.g. file/image) render a display
    // shape that isn't a valid write payload on its own, so only send a
    // value when the user actually edited something.
    if (!dirty) { closeAndBlur(); return }
    try {
      await onSave(value)
      setDirty(false)
      setPreviewMessages([])
      closeAndBlur()
    } catch {
      // error surfaced via `errors` prop; keep the popup open so the user can retry
    }
  }

  function cancel() {
    setValue(savedValue)
    setDirty(false)
    setPreviewMessages([])
    closeAndBlur()
  }

  // Show both: the live preview (while editing) and the last known
  // post-save result (errors/warnings from the previous save) — neither
  // should hide the other, before or after saving.
  const displayedMessages = [...errors, ...previewMessages]

  const large = LARGE_TYPES.has(fd.data_type)

  const previewNode = savedValue == null
    ? <span style={{ color: '#9ca3af' }}>—</span>
    : <FieldPreview fd={fd} value={savedValue} lang={uiLang} entityChildren={entity.children as Record<string, unknown[]>} />

  if (!editable) {
    if (savedValue == null) return null
    return previewNode
  }

  return (
    // The click-to-open handler lives only on the trigger span, not on a
    // wrapper that also contains <OverlayPanel> — React bubbles synthetic
    // events through the *component* tree even across a portal, so a click
    // inside the popup's (portaled) content would otherwise re-trigger this
    // handler and immediately toggle the panel closed again.
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.2rem' }}>
      <span
        onClick={e => opRef.current?.toggle(e)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.2rem', cursor: 'pointer',
          padding: '0.15rem 0.3rem', margin: '-0.15rem -0.3rem', borderRadius: '4px',
          background: hovered ? '#eff6ff' : undefined,
          outline: hovered ? '1px dashed #93c5fd' : undefined,
        }}
      >
        {previewNode}
      </span>
      <OverlayPanel ref={opRef} style={{ width: '50vw' }} onHide={() => { setPreviewMessages([]); onClearErrors() }}>
        {displayedMessages.length > 0 && <PolicyMessageList messages={displayedMessages} />}
        <FieldCommitWrapper
          dirty={dirty}
          saving={saving}
          large={large}
          blurCommit={BLUR_COMMIT_TYPES.has(fd.data_type)}
          disabled={!editable}
          alwaysShowButtons={fd.data_type !== 'workflow'}
          onCommit={() => void commit()}
          onCancel={cancel}
        >
          <FieldInput
            fd={fd}
            value={value}
            onChange={v => { setValue(v); setDirty(true) }}
            disabled={!editable}
            lang={uiLang}
            nodeId={entity.id}
            onEntityRefresh={onEntityRefresh}
            entityChildren={entity.children as Record<string, unknown[]>}
          />
        </FieldCommitWrapper>
      </OverlayPanel>
    </span>
  )
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function getFieldVal(entity: EntityOut, slug: string, uiLang: string): unknown {
  return (
    entity.field_values.find(v => v.field_slug === slug && v.language === uiLang)?.value
    ?? entity.field_values.find(v => v.field_slug === slug && v.language === '')?.value
    ?? entity.field_values.find(v => v.field_slug === slug)?.value
    ?? null
  )
}

function entityPreviewLabel(entity: EntityOut, config: ConfigVersionOut | null | undefined, uiLang: string): string {
  if (!config) return entity.id.slice(0, 8)
  const defaultLang = config.languages.find(l => l.is_default)?.code ?? uiLang
  const previewFds = config.fields.filter(f => f.is_preview && !SKIP_DATA_TYPES.has(f.data_type))
  if (!previewFds.length) return entity.id.slice(0, 8)
  const parts: string[] = []
  for (const fd of previewFds) {
    const val = getFieldVal(entity, fd.slug, defaultLang)
    if (val == null) continue
    const t = fieldPreviewText(fd, val, defaultLang)
    if (t) parts.push(t)
  }
  return parts.join(' · ') || entity.id.slice(0, 8)
}

// ── Component ──────────────────────────────────────────────────────────────────

export function UdmDashboard() {
  const { i18n } = useTranslation()
  const navigate = useNavigate()
  const uiLang = i18n.language.split('-')[0]

  const [types, setTypes] = useState<UDMTypeOut[]>([])
  const [configByTypeId, setConfigByTypeId] = useState<Record<string, ConfigVersionOut>>({})
  const [entitiesByTypeId, setEntitiesByTypeId] = useState<Record<string, EntityOut[]>>({})
  const [loadingTypeIds, setLoadingTypeIds] = useState<Set<string>>(new Set())
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({})
  const [selectedColumns, setSelectedColumns] = useState<string[]>([])
  const [typesLoading, setTypesLoading] = useState(true)
  // `filterText` is what the user types; `appliedFilter` is what the server saw.
  const [filterText, setFilterText] = useState('')
  const [appliedFilter, setAppliedFilter] = useState('')
  const [filterError, setFilterError] = useState<string | null>(null)
  const [editMode, setEditMode] = useState(false)
  const [savingCells, setSavingCells] = useState<Set<string>>(new Set())
  const [cellErrors, setCellErrors] = useState<Record<string, PolicyMessage[]>>({})

  useEffect(() => {
    udmListTypes()
      .then(setTypes)
      .catch(() => {})
      .finally(() => setTypesLoading(false))
  }, [])

  const expandedTypeIds = useMemo(
    () => types.map(t => t.id).filter(id => expandedKeys[id]),
    [expandedKeys, types],
  )

  // Field columns: union of expanded types' config fields
  const fieldColumns = useMemo((): FieldColOpt[] => {
    const seen = new Set<string>()
    const result: FieldColOpt[] = []
    for (const typeId of expandedTypeIds) {
      for (const fd of configByTypeId[typeId]?.fields ?? []) {
        if (!seen.has(fd.slug) && !SKIP_DATA_TYPES.has(fd.data_type)) {
          seen.add(fd.slug)
          result.push({ kind: 'field', id: fd.slug, slug: fd.slug, label: getLang(fd.label as Record<string, string>, uiLang) || fd.slug })
        }
      }
    }
    return result
  }, [expandedTypeIds, configByTypeId, uiLang])

  // Dashboard columns: union of keys from all loaded entities across expanded types
  const dashColumns = useMemo((): DashColOpt[] => {
    const seen = new Map<string, DashColOpt>()
    for (const typeId of expandedTypeIds) {
      for (const entity of entitiesByTypeId[typeId] ?? []) {
        for (const col of entity.dashboard_columns ?? []) {
          if (!seen.has(col.key)) {
            seen.set(col.key, { kind: 'dash', id: dashId(col.key), key: col.key, label: col.label, renderer: col.renderer })
          }
        }
      }
    }
    return [...seen.values()]
  }, [expandedTypeIds, entitiesByTypeId])

  const allColumns: ColOpt[] = [...fieldColumns, ...dashColumns]

  // Default selection: preview field slugs + all dashboard column IDs
  const defaultSelectedIds = useMemo((): string[] => {
    const readyTypeIds = expandedTypeIds.filter(id => configByTypeId[id])
    if (!readyTypeIds.length) return dashColumns.map(c => c.id)

    const sets = readyTypeIds.map(id =>
      new Set(
        configByTypeId[id].fields
          .filter(f => f.is_preview && !SKIP_DATA_TYPES.has(f.data_type))
          .map(f => f.slug),
      ),
    )
    const fieldIntersection = sets.reduce((a, b) => new Set([...a].filter(x => b.has(x))))
    return [...fieldIntersection, ...dashColumns.map(c => c.id)]
  }, [expandedTypeIds, configByTypeId, dashColumns])

  // Reset selection when expanded types or dashboard columns change
  useEffect(() => {
    setSelectedColumns(defaultSelectedIds)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultSelectedIds.join(',')])

  // Replace one entity in place after a cell save or workflow transition, so
  // the row reflects fresh values/policy state without a full type reload.
  const patchEntityInState = useCallback((typeId: string, updated: EntityOut) => {
    setEntitiesByTypeId(prev => ({
      ...prev,
      [typeId]: (prev[typeId] ?? []).map(e => (e.id === updated.id ? updated : e)),
    }))
  }, [])

  function cellKey(entityId: string, slug: string) {
    return `${entityId}:${slug}`
  }

  async function saveCell(typeId: string, entity: EntityOut, slug: string, value: unknown) {
    const key = cellKey(entity.id, slug)
    setSavingCells(prev => new Set(prev).add(key))
    setCellErrors(prev => { const n = { ...prev }; delete n[key]; return n })
    try {
      const updated = await udmPatchEntity(entity.id, { [slug]: value })
      patchEntityInState(typeId, updated)
      // A successful save can still carry policy warnings/info for this
      // field (e.g. "close to the limit") — surface those too, not just
      // hard save errors.
      const relevant = ((updated.policy_messages ?? []) as PolicyMessage[]).filter(m => isForField(m, slug))
      if (relevant.length > 0) setCellErrors(prev => ({ ...prev, [key]: relevant }))
    } catch (e) {
      setCellErrors(prev => ({ ...prev, [key]: errorToFieldMessages(e, slug) }))
      throw e
    } finally {
      setSavingCells(prev => { const n = new Set(prev); n.delete(key); return n })
    }
  }

  // Refetches one entity — used after a workflow transition, whose result
  // (unlike a field patch) doesn't hand back the updated entity directly.
  async function refreshEntity(typeId: string, entityId: string) {
    const updated = await udmGetEntity(entityId)
    patchEntityInState(typeId, updated)
  }

  const loadEntities = useCallback((typeId: string, query: string) => {
    setLoadingTypeIds(prev => new Set([...prev, typeId]))
    udmListEntitiesByType(typeId, 200, query)
      .then(entities => {
        setEntitiesByTypeId(prev => ({ ...prev, [typeId]: entities }))
        setFilterError(null)
      })
      .catch((err: unknown) => {
        setEntitiesByTypeId(prev => ({ ...prev, [typeId]: [] }))
        // A rejected query is a property of the query, not of the type — one
        // message for the whole dashboard.
        if (query) setFilterError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setLoadingTypeIds(prev => { const n = new Set(prev); n.delete(typeId); return n }))
  }, [])

  // Re-run every already-expanded type against a newly applied filter.
  useEffect(() => {
    setFilterError(null)
    for (const typeId of expandedTypeIds) loadEntities(typeId, appliedFilter)
  // Only on filter change: expanding a type loads it via handleExpand.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appliedFilter])

  function handleExpand(event: { node: TreeNode }) {
    const typeId = event.node.key as string
    if (!configByTypeId[typeId]) {
      udmGetTypeConfig(typeId)
        .then(config => setConfigByTypeId(prev => ({ ...prev, [typeId]: config })))
        .catch(() => {})
    }
    if (!entitiesByTypeId[typeId]) loadEntities(typeId, appliedFilter)
  }

  const treeNodes: TreeNode[] = types.map(type => ({
    key: type.id,
    data: { kind: 'type', type, loading: loadingTypeIds.has(type.id) } as RowData,
    leaf: false,
    children: (entitiesByTypeId[type.id] ?? []).map(entity => ({
      key: entity.id,
      data: { kind: 'entity', entity, config: configByTypeId[type.id] ?? null } as RowData,
      leaf: true,
    })),
  }))

  const visibleColumns = allColumns.filter(c => selectedColumns.includes(c.id))

  // Column widths: dashboard columns are wider to fit charts
  function colStyle(col: ColOpt) {
    if (col.kind === 'dash') return { minWidth: '180px', width: '200px' }
    return { width: '160px', minWidth: '130px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }
  }

  function tableMinWidth() {
    const base = 220  // label column
    return visibleColumns.reduce((sum, c) => sum + (c.kind === 'dash' ? 200 : 160), base)
  }

  // The slug under the label is what the filter query expects as a field name.
  function columnHeader(col: ColOpt) {
    const slug = col.kind === 'field' ? col.slug : col.key
    return (
      <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
        <span>{col.label}</span>
        <code style={{ fontSize: '0.7rem', fontWeight: 400, color: '#6b7280' }}>{slug}</code>
      </div>
    )
  }

  // ── Cell renderers ───────────────────────────────────────────────────────────

  function labelBody(node: TreeNode) {
    const d = node.data as RowData
    if (d.kind === 'type') {
      return (
        <span style={{ fontWeight: 600 }}>
          {d.type.label || d.type.name}
          {d.loading && <span style={{ marginLeft: '0.5rem', color: '#888', fontSize: '0.8rem' }}>Loading…</span>}
        </span>
      )
    }
    return (
      <button
        type="button"
        style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#1d4ed8', padding: 0, textAlign: 'left' }}
        onClick={() => navigate(`/udm-entity/${d.entity.id}`)}
      >
        {entityPreviewLabel(d.entity, d.config, uiLang)}
      </button>
    )
  }

  function makeColBody(col: ColOpt) {
    return (node: TreeNode) => {
      const d = node.data as RowData
      if (d.kind === 'type') return null

      if (col.kind === 'field') {
        const fd = d.config?.fields.find(f => f.slug === col.slug)
        if (!fd) return null
        const val = getFieldVal(d.entity, col.slug, uiLang)

        if (editMode && !isUneditableInGrid(fd.data_type)) {
          const editableSlugs = new Set(
            (d.entity.editable_fields as unknown as Record<string, string[]>)?.[d.entity.id] ?? [],
          )
          const editable = editableSlugs.has(col.slug)
          // Non-editable fields with no value are simply not viewable — nothing to show.
          if (val == null && !editable) return null
          const typeId = d.entity.user_defined_model_type_id as string
          const key = cellKey(d.entity.id, col.slug)
          return (
            <DashboardFieldCell
              fd={fd}
              entity={d.entity}
              uiLang={uiLang}
              editable={editable}
              saving={savingCells.has(key)}
              errors={cellErrors[key] ?? []}
              onSave={value => saveCell(typeId, d.entity, col.slug, value)}
              onEntityRefresh={() => void refreshEntity(typeId, d.entity.id)}
              onClearErrors={() => setCellErrors(prev => { const n = { ...prev }; delete n[key]; return n })}
            />
          )
        }

        if (val == null) return null
        return <FieldPreview fd={fd} value={val} lang={uiLang} entityChildren={d.entity.children as Record<string, unknown[]>} />
      }

      // Dashboard column: find matching entry from this entity's dashboard_columns
      const dashCol = (d.entity.dashboard_columns ?? []).find(c => c.key === col.key)
      if (!dashCol) return null
      return <DashboardCell col={dashCol as DashboardColumnOut} entity={d.entity} />
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={{ padding: '1.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 700 }}>UDM Dashboard</h1>
        <ToggleButton
          checked={editMode}
          onChange={e => setEditMode(e.value)}
          onLabel="Editing"
          offLabel="Edit mode"
          onIcon="pi pi-pencil"
          offIcon="pi pi-pencil"
          className="p-button-sm"
        />
        <form
          onSubmit={e => { e.preventDefault(); setAppliedFilter(filterText) }}
          style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}
        >
          <InputText
            value={filterText}
            onChange={e => setFilterText(e.target.value)}
            placeholder='Filter, e.g. city:Berlin AND none(participants: status:rejected)'
            title={FILTER_HELP}
            style={{ minWidth: '320px', borderColor: filterError ? '#dc2626' : undefined }}
          />
          <Button type="submit" label="Filter" size="small" />
          {(filterText || appliedFilter) && (
            <Button
              type="button"
              label="Clear"
              size="small"
              outlined
              onClick={() => { setFilterText(''); setAppliedFilter('') }}
            />
          )}
        </form>
        {allColumns.length > 0 && (
          <MultiSelect
            value={selectedColumns}
            options={allColumns.map(c => ({ label: c.label, value: c.id }))}
            onChange={e => setSelectedColumns(e.value as string[])}
            placeholder="Select columns"
            display="chip"
            style={{ minWidth: '250px', maxWidth: '480px' }}
          />
        )}
      </div>

      {filterError && (
        <div
          role="alert"
          style={{
            marginBottom: '1rem', padding: '0.5rem 0.75rem', borderRadius: '4px',
            background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', fontSize: '0.85rem',
          }}
        >
          {filterError}
        </div>
      )}

      {typesLoading && <p style={{ color: '#888' }}>Loading…</p>}
      {!typesLoading && types.length === 0 && <p style={{ color: '#888' }}>No UDM types available.</p>}
      {!typesLoading && types.length > 0 && (
        <div style={{ overflowX: 'auto', width: '100%' }}>
          <TreeTable
            value={treeNodes}
            expandedKeys={expandedKeys}
            onToggle={e => setExpandedKeys(e.value)}
            onExpand={handleExpand}
            tableStyle={{ minWidth: `${tableMinWidth()}px`, tableLayout: 'fixed' }}
            scrollable
            scrollHeight="calc(100vh - 220px)"
          >
            <Column
              header="Label"
              body={labelBody}
              expander
              style={{ width: '220px', minWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            />
            {visibleColumns.map(col => (
              <Column
                key={col.id}
                header={columnHeader(col)}
                body={makeColBody(col)}
                style={colStyle(col)}
              />
            ))}
          </TreeTable>
        </div>
      )}
    </div>
  )
}
