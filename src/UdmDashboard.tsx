import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { TreeTable } from 'primereact/treetable'
import { Column } from 'primereact/column'
import { MultiSelect } from 'primereact/multiselect'
import type { TreeNode } from 'primereact/treenode'
import {
  udmListTypes,
  udmGetTypeConfig,
  udmListEntitiesByType,
  type UDMTypeOut,
  type ConfigVersionOut,
  type EntityOut,
  type DashboardColumnOut,
} from './apiUdm'
import { getLang, FieldPreview, fieldPreviewText } from './udm-editors'

// ── Constants ──────────────────────────────────────────────────────────────────

const SKIP_DATA_TYPES = new Set([
  'tab_container', 'tab', 'save_button', 'hstack', 'hstack_group', 'tab_prev', 'tab_next',
])

const DASH_PREFIX = '__dash__:'
function dashId(key: string) { return `${DASH_PREFIX}${key}` }
function isDashId(id: string) { return id.startsWith(DASH_PREFIX) }
function dashKey(id: string) { return id.slice(DASH_PREFIX.length) }

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

function DashboardCell({ col, entity }: { col: DashboardColumnOut; entity: EntityOut }) {
  switch (col.renderer) {
    case 'progress_bar': return <ProgressBarCell value={col.value} />
    case 'meter':        return <MeterCell value={col.value} />
    default:             return <span style={{ fontSize: '0.85rem' }}>{String(col.value ?? '')}</span>
  }
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

  function handleExpand(event: { node: TreeNode }) {
    const typeId = event.node.key as string
    if (!configByTypeId[typeId]) {
      udmGetTypeConfig(typeId)
        .then(config => setConfigByTypeId(prev => ({ ...prev, [typeId]: config })))
        .catch(() => {})
    }
    if (!entitiesByTypeId[typeId]) {
      setLoadingTypeIds(prev => new Set([...prev, typeId]))
      udmListEntitiesByType(typeId)
        .then(entities => setEntitiesByTypeId(prev => ({ ...prev, [typeId]: entities })))
        .catch(() => setEntitiesByTypeId(prev => ({ ...prev, [typeId]: [] })))
        .finally(() => setLoadingTypeIds(prev => { const n = new Set(prev); n.delete(typeId); return n }))
    }
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
        if (val == null) return null
        return <FieldPreview fd={fd} value={val} lang={uiLang} entityChildren={d.entity.children as Record<string, unknown[]>} />
      }

      // Dashboard column: find matching entry from this entity's dashboard_columns
      const dashCol = (d.entity.dashboard_columns ?? []).find(c => c.key === col.key)
      if (!dashCol) return null
      return <DashboardCell col={dashCol} entity={d.entity} />
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={{ padding: '1.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 700 }}>UDM Dashboard</h1>
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
                header={col.label}
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
