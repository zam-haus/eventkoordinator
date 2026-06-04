import { useCallback, useEffect, useRef, useState } from 'react'
import { ColorPicker, type ColorPickerChangeEvent } from 'primereact/colorpicker'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  reconnectEdge,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
  Handle,
  Position,
  MarkerType,
  ConnectionMode,
  ReactFlowProvider,
  useReactFlow,
  Panel,
  applyNodeChanges,
  applyEdgeChanges,
  type NodeChange,
  type EdgeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { getCsrfToken } from './api'
import styles from './WorkflowEditor.module.css'

// ─── WCAG contrast helper ─────────────────────────────────────────────────────

function wcagTextColor(hex: string): string {
  const h = hex.replace('#', '')
  if (h.length !== 6) return '#000000'
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  const lin = (c: number) => { const v = c / 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
  const L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
  return L < 0.1791 ? '#ffffff' : '#000000'
}

// ─── Types ───────────────────────────────────────────────────────────────────

interface WfStateData {
  label: string        // display string for canvas (primary language)
  labels: Record<string, string>  // full localized label dict
  name: string
  isInitial: boolean
  usageCount?: number
  isRetiring?: boolean
  backgroundColor?: string
  textColor?: string
  [key: string]: unknown
}

interface WfEdgeData {
  label: string        // display string for canvas (primary language)
  labels?: Record<string, string>  // full localized label dict
  name?: string        // current transition slug (may differ from stable edge.id after rename)
  [key: string]: unknown
}

// All nodes share Record<string,unknown> data so special nodes can coexist.
type AnyWfNode = Node<Record<string, unknown>>
type WfEdge = Edge<WfEdgeData>

// ─── Language helpers ─────────────────────────────────────────────────────────

const LANG_PATTERN = /^[a-z]{2,3}(-[A-Za-z0-9]+)*$/

function deriveLanguages(nodes: AnyWfNode[], edges: WfEdge[]): string[] {
  const langs = new Set<string>()
  for (const n of nodes) {
    if (n.type === 'workflowState') {
      const labels = (n.data as WfStateData).labels ?? {}
      Object.keys(labels).forEach(k => langs.add(k))
    }
  }
  for (const e of edges) {
    const labels = (e.data as WfEdgeData).labels ?? {}
    Object.keys(labels).forEach(k => langs.add(k))
  }
  return langs.size > 0 ? Array.from(langs) : ['en']
}

function pickDisplayLabel(labels: Record<string, string>, primary: string, fallback: string): string {
  return labels[primary] ?? Object.values(labels).find(v => v) ?? fallback
}

// Remove empty-string values; ensure at least one entry so backend min_length=1 passes
function cleanLabels(labels: Record<string, string>, fallbackKey: string, fallbackVal: string): Record<string, string> {
  const out = Object.fromEntries(Object.entries(labels).filter(([, v]) => v && v.trim()))
  return Object.keys(out).length > 0 ? out : { [fallbackKey]: fallbackVal }
}

// IDs for the singleton special nodes
const ID_FROM_ANY = '__from_any__'
const ID_FROM_UNDEFINED = '__from_undefined__'
const ID_KEEP_STATE = '__keep_state__'

function nodeStateData(n: AnyWfNode): WfStateData {
  return n.data as unknown as WfStateData
}

interface WorkflowStateOut {
  name: string
  label: Record<string, string>
  is_initial: boolean
  position_x: number
  position_y: number
  background_color: string
  text_color: string
}

interface WorkflowTransitionOut {
  name: string
  label: Record<string, string>
  from_state: string | null
  from_undefined_only: boolean
  to_state: string
  source_handle: string
  target_handle: string
}

interface WorkflowDefinitionOut {
  id: string
  name: string
  description: string
  initial_state: string | null
  states: WorkflowStateOut[]
  transitions: WorkflowTransitionOut[]
  virtual_node_positions?: Record<string, unknown>
  draft_version_id?: string | null
  published_version_id?: string | null
}

// ─── API helpers ──────────────────────────────────────────────────────────────

async function apiFetch(url: string, method: string, body?: unknown) {
  const token = await getCsrfToken()
  const res = await fetch(url, {
    method,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-CSRFToken': token } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return res
}

async function listWorkflows(): Promise<WorkflowDefinitionOut[]> {
  const res = await apiFetch('/api/udm/workflows/', 'GET')
  if (!res.ok) throw new Error('Failed to load workflows')
  return res.json()
}

async function createWorkflow(payload: unknown): Promise<WorkflowDefinitionOut> {
  const res = await apiFetch('/api/udm/workflows/', 'POST', payload)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(JSON.stringify(err))
  }
  return res.json()
}

async function updateWorkflow(id: string, payload: unknown): Promise<WorkflowDefinitionOut> {
  const res = await apiFetch(`/api/udm/workflows/${id}/`, 'PUT', payload)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(JSON.stringify(err))
  }
  return res.json()
}

async function deleteWorkflow(id: string): Promise<void> {
  const res = await apiFetch(`/api/udm/workflows/${id}/`, 'DELETE')
  if (!res.ok && res.status !== 204) throw new Error('Failed to delete workflow')
}

async function publishWorkflowDraft(id: string): Promise<WorkflowDefinitionOut> {
  const res = await apiFetch(`/api/udm/workflows/${id}/versions/draft/publish/`, 'POST')
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(JSON.stringify(err))
  }
  return res.json()
}

async function fetchStateCounts(workflowId: string): Promise<Record<string, number>> {
  const res = await apiFetch(`/api/udm/workflows/${workflowId}/state-counts/`, 'GET')
  if (!res.ok) return {}
  return res.json()
}

// ─── Conversions ──────────────────────────────────────────────────────────────

const NODE_WIDTH = 240
const NODE_HEIGHT = 135

function wfToReactFlow(wf: WorkflowDefinitionOut): { nodes: AnyWfNode[]; edges: WfEdge[] } {
  const stateNodes: AnyWfNode[] = wf.states.map((s) => ({
    id: s.name,
    type: 'workflowState',
    position: { x: s.position_x, y: s.position_y },
    data: {
      label: s.label['en'] ?? Object.values(s.label)[0] ?? s.name,
      labels: { ...s.label },
      name: s.name,
      isInitial: s.is_initial,
      backgroundColor: s.background_color ?? '#ffffff',
      textColor: s.text_color ?? '#000000',
    } as Record<string, unknown>,
  }))

  // Calculate bounding box to place special nodes sensibly
  const xs = stateNodes.map((n) => n.position.x)
  const ys = stateNodes.map((n) => n.position.y)
  const minX = xs.length ? Math.min(...xs) : 0
  const maxX = xs.length ? Math.max(...xs) : 400
  const midY = ys.length ? (Math.min(...ys) + Math.max(...ys)) / 2 : 100

  const nodes: AnyWfNode[] = [...stateNodes]
  const edges: WfEdge[] = []
  let hasFromAny = false
  let hasFromUndefined = false
  let hasKeepState = false

  for (const t of wf.transitions) {
    const displayLabel = t.label['en'] ?? Object.values(t.label)[0] ?? t.name
    const edgeBase: Omit<WfEdge, 'id' | 'source' | 'target' | 'sourceHandle' | 'targetHandle'> = {
      data: { label: displayLabel, labels: { ...t.label }, name: t.name },
      label: displayLabel,
      markerEnd: { type: MarkerType.ArrowClosed },
      type: 'smoothstep',
      reconnectable: true,
    }

    if (t.from_state === null && !t.from_undefined_only) {
      // From Any
      if (!hasFromAny) {
        const saved = (wf.virtual_node_positions as Record<string, { x: number; y: number } | undefined>)?.['fromAny']
        nodes.push({ id: ID_FROM_ANY, type: 'fromAny', position: saved ?? { x: minX - 200, y: midY - 40 }, data: {} })
        hasFromAny = true
      }
      edges.push({ ...edgeBase, id: t.name, source: ID_FROM_ANY, target: t.to_state, sourceHandle: t.source_handle || null, targetHandle: t.target_handle || null })
    } else if (t.from_state === null && t.from_undefined_only) {
      // From Undefined
      if (!hasFromUndefined) {
        const saved = (wf.virtual_node_positions as Record<string, { x: number; y: number } | undefined>)?.['fromUndefined']
        nodes.push({ id: ID_FROM_UNDEFINED, type: 'fromUndefined', position: saved ?? { x: minX - 200, y: midY + 40 }, data: {} })
        hasFromUndefined = true
      }
      edges.push({ ...edgeBase, id: t.name, source: ID_FROM_UNDEFINED, target: t.to_state, sourceHandle: t.source_handle || null, targetHandle: t.target_handle || null })
    } else if (t.from_state !== null && t.from_state === t.to_state) {
      // Keep State (self-loop)
      if (!hasKeepState) {
        nodes.push({ id: ID_KEEP_STATE, type: 'keepState', position: { x: maxX + NODE_WIDTH + 50, y: midY - 30 }, data: {} })
        hasKeepState = true
      }
      edges.push({ ...edgeBase, id: t.name, source: t.from_state, target: ID_KEEP_STATE, sourceHandle: t.source_handle || null, targetHandle: t.target_handle || null })
    } else {
      edges.push({ ...edgeBase, id: t.name, source: t.from_state ?? '', target: t.to_state, sourceHandle: t.source_handle || null, targetHandle: t.target_handle || null })
    }
  }

  return { nodes, edges }
}

function reactFlowToWf(
  nodes: AnyWfNode[],
  edges: WfEdge[],
  name: string,
  description: string,
  primaryLang: string = 'en',
) {
  const stateNodes = nodes.filter((n) => n.type === 'workflowState')

  // Map stable node.id → current data.name (to resolve renamed states in edge references)
  const idToName = new Map(stateNodes.map((n) => [n.id, nodeStateData(n).name ?? n.id]))

  const transitions = edges
    .filter((e) => e.source && e.target)
    .map((e) => {
      const srcType = nodes.find((n) => n.id === e.source)?.type
      const tgtType = nodes.find((n) => n.id === e.target)?.type

      let from_state: string | null = idToName.get(e.source) ?? e.source
      let from_undefined_only = false
      let to_state = idToName.get(e.target) ?? e.target

      if (srcType === 'fromAny') {
        from_state = null
        from_undefined_only = false
      } else if (srcType === 'fromUndefined') {
        from_state = null
        from_undefined_only = true
      }

      if (tgtType === 'keepState') {
        // Self-loop: to_state = from_state
        to_state = from_state ?? ''
      }

      // Skip edges that reference missing real states
      const resolvedValidNames = new Set(idToName.values())
      if (to_state === '' || !resolvedValidNames.has(to_state)) return null
      if (from_state !== null && !resolvedValidNames.has(from_state)) return null

      const eData = e.data as WfEdgeData | undefined
      const transName = eData?.name ?? e.id
      return {
        name: transName,
        label: cleanLabels(
          eData?.labels ?? { [primaryLang]: String(eData?.label ?? transName) },
          primaryLang, transName,
        ),
        from_state,
        from_undefined_only,
        to_state,
        source_handle: e.sourceHandle ?? '',
        target_handle: e.targetHandle ?? '',
      }
    })
    .filter(Boolean)

  const virtual_node_positions: Record<string, { x: number; y: number }> = {}
  for (const n of nodes) {
    if (n.type === 'fromAny' || n.type === 'fromUndefined') {
      virtual_node_positions[n.type] = { x: n.position.x, y: n.position.y }
    }
  }

  return {
    name,
    description,
    states: stateNodes.map((n) => {
      const d = nodeStateData(n)
      const stateName = d.name ?? n.id
      const previousName = stateName !== n.id ? n.id : undefined
      return {
        name: stateName,
        ...(previousName !== undefined ? { previous_name: previousName } : {}),
        label: cleanLabels(d.labels ?? { [primaryLang]: d.label }, primaryLang, stateName),
        is_initial: d.isInitial,
        position_x: n.position.x,
        position_y: n.position.y,
        background_color: d.backgroundColor ?? '#ffffff',
      }
    }),
    transitions,
    virtual_node_positions,
  }
}

// ─── Proposal workflow example ────────────────────────────────────────────────

const mkEdge = (id: string, src: string, tgt: string, sh: string, th: string, lbl: string): WfEdge => ({
  id,
  source: src,
  target: tgt,
  sourceHandle: sh,
  targetHandle: th,
  data: { label: lbl, labels: { en: lbl }, name: id },
  label: lbl,
  markerEnd: { type: MarkerType.ArrowClosed },
  type: 'smoothstep',
  reconnectable: true,
})

const PROPOSAL_EXAMPLE: { nodes: AnyWfNode[]; edges: WfEdge[] } = {
  nodes: [
    { id: 'draft',     type: 'workflowState', position: { x: 60,  y: 220 }, data: { label: 'Draft',     labels: { en: 'Draft'     }, name: 'draft',     isInitial: true,  backgroundColor: '#ffffff', textColor: '#000000' } },
    { id: 'submitted', type: 'workflowState', position: { x: 380, y: 100 }, data: { label: 'Submitted', labels: { en: 'Submitted' }, name: 'submitted', isInitial: false, backgroundColor: '#ffffff', textColor: '#000000' } },
    { id: 'revise',    type: 'workflowState', position: { x: 380, y: 340 }, data: { label: 'Revise',    labels: { en: 'Revise'    }, name: 'revise',    isInitial: false, backgroundColor: '#ffffff', textColor: '#000000' } },
    { id: 'accepted',  type: 'workflowState', position: { x: 700, y: 20  }, data: { label: 'Accepted',  labels: { en: 'Accepted'  }, name: 'accepted',  isInitial: false, backgroundColor: '#ffffff', textColor: '#000000' } },
    { id: 'rejected',  type: 'workflowState', position: { x: 700, y: 220 }, data: { label: 'Rejected',  labels: { en: 'Rejected'  }, name: 'rejected',  isInitial: false, backgroundColor: '#ffffff', textColor: '#000000' } },
  ],
  edges: [
    mkEdge('submit',           'draft',     'submitted', 'right-top',    'left-top',     'Submit'),
    mkEdge('resubmit',         'revise',    'submitted', 'top-center',   'bottom-center','Resubmit'),
    mkEdge('request-revision', 'submitted', 'revise',    'bottom-center','top-center',   'Request Revision'),
    mkEdge('allow-revision',   'rejected',  'revise',    'bottom-left',  'right-bottom', 'Allow Revision'),
    mkEdge('accept',           'submitted', 'accepted',  'right-top',    'left-top',     'Accept'),
    mkEdge('reject',           'submitted', 'rejected',  'right-bottom', 'left-top',     'Reject'),
  ],
}

// ─── Custom 16:9 state node ───────────────────────────────────────────────────

function WorkflowStateNode({ data, selected }: NodeProps) {
  const d = data as WfStateData
  const bg = d.backgroundColor ?? '#ffffff'
  const fg = d.textColor ?? '#000000'
  return (
    <div
      className={[styles.wfNode, selected ? styles.wfNodeSelected : '', d.isInitial ? styles.wfNodeInitial : ''].join(' ')}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT, backgroundColor: bg, color: fg, borderColor: selected ? undefined : (d.isInitial ? undefined : bg === '#ffffff' ? undefined : bg) }}
    >
      <Handle type="source" position={Position.Top}    id="top-left"      style={{ left: '25%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Top}    id="top-center"    style={{ left: '50%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Top}    id="top-right"     style={{ left: '75%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Bottom} id="bottom-left"   style={{ left: '25%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Bottom} id="bottom-center" style={{ left: '50%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Bottom} id="bottom-right"  style={{ left: '75%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Left}   id="left-top"      style={{ top:  '33%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Left}   id="left-bottom"   style={{ top:  '67%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Right}  id="right-top"     style={{ top:  '33%' }}  className={styles.handle} />
      <Handle type="source" position={Position.Right}  id="right-bottom"  style={{ top:  '67%' }}  className={styles.handle} />
      <div className={styles.wfNodeContent}>
        {d.isInitial && <span className={styles.initialBadge} style={{ color: d.isInitial && (d.backgroundColor ?? '#ffffff') !== '#ffffff' ? fg : undefined }}>●</span>}
        <span className={styles.wfNodeLabel} style={{ color: fg }}>{d.label}</span>
        <span className={styles.wfNodeSlug} style={{ color: fg, opacity: 0.6 }}>{d.name}</span>
      </div>
      {d.isRetiring && <span className={styles.retiringBadge}>RETIRING</span>}
      {d.usageCount !== undefined && (
        <span className={`${styles.countBadge} ${d.usageCount > 0 ? styles.countBadgeActive : styles.countBadgeMuted}`}>
          {d.usageCount}
        </span>
      )}
    </div>
  )
}

// ─── Special nodes (smaller, 4 handles one per side) ─────────────────────────

function SpecialNodeHandles({ type }: { type: 'source' | 'target' }) {
  return (
    <>
      <Handle type={type} position={Position.Top}    id="top"    className={styles.handle} />
      <Handle type={type} position={Position.Bottom} id="bottom" className={styles.handle} />
      <Handle type={type} position={Position.Left}   id="left"   className={styles.handle} />
      <Handle type={type} position={Position.Right}  id="right"  className={styles.handle} />
    </>
  )
}

function FromAnyNode({ selected }: NodeProps) {
  return (
    <div className={[styles.specialNode, styles.fromAnyNode, selected ? styles.wfNodeSelected : ''].join(' ')}>
      <SpecialNodeHandles type="source" />
      <span className={styles.specialNodeLabel}>From Any</span>
      <span className={styles.specialNodeSub}>any state → …</span>
    </div>
  )
}

function FromUndefinedNode({ selected }: NodeProps) {
  return (
    <div className={[styles.specialNode, styles.fromUndefinedNode, selected ? styles.wfNodeSelected : ''].join(' ')}>
      <SpecialNodeHandles type="source" />
      <span className={styles.specialNodeLabel}>From Undefined</span>
      <span className={styles.specialNodeSub}>null state → …</span>
    </div>
  )
}

function KeepStateNode({ selected }: NodeProps) {
  return (
    <div className={[styles.specialNode, styles.keepStateNode, selected ? styles.wfNodeSelected : ''].join(' ')}>
      <SpecialNodeHandles type="target" />
      <span className={styles.specialNodeLabel}>Keep State</span>
      <span className={styles.specialNodeSub}>… → same state</span>
    </div>
  )
}

const NODE_TYPES = {
  workflowState: WorkflowStateNode,
  fromAny: FromAnyNode,
  fromUndefined: FromUndefinedNode,
  keepState: KeepStateNode,
}

// ─── Slug helpers ─────────────────────────────────────────────────────────────

function toSlug(text: string, prefix: string): string {
  const base = text.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^[^a-z]+/, '').replace(/_+$/, '')
  return base.length > 0 ? base : `${prefix}_${Math.random().toString(36).slice(2, 7)}`
}

function uniqueSlug(base: string, existing: Set<string>): string {
  if (!existing.has(base)) return base
  let i = 2
  while (existing.has(`${base}_${i}`)) i++
  return `${base}_${i}`
}

// ─── Properties panel ─────────────────────────────────────────────────────────

interface PropsPanelProps {
  nodes: AnyWfNode[]
  edges: WfEdge[]
  selectedNodeIds: string[]
  selectedEdgeIds: string[]
  onNodeChange: (id: string, patch: Partial<WfStateData>) => void
  onEdgeChange: (id: string, patch: Partial<WfEdgeData>) => void
  onDeleteSelected: () => void
  orphanedStates: WorkflowStateOut[]
  stateCounts: Record<string, number>
  onReadd: (state: WorkflowStateOut) => void
  languages: string[]
  onAddLanguage: (lang: string) => void
  onRemoveLanguage: (lang: string) => void
}

const SPECIAL_DESCRIPTIONS: Record<string, string> = {
  [ID_FROM_ANY]:       'Transitions connecting here fire from any workflow state.',
  [ID_FROM_UNDEFINED]: 'Transitions connecting here fire only when the field has no state yet (null).',
  [ID_KEEP_STATE]:     'Transitions from a state to this node keep that state unchanged (self-loop).',
}

function PropertiesPanel({ nodes, edges, selectedNodeIds, selectedEdgeIds, onNodeChange, onEdgeChange, onDeleteSelected, orphanedStates, stateCounts, onReadd, languages, onAddLanguage, onRemoveLanguage }: PropsPanelProps) {
  const [addingLang, setAddingLang] = useState(false)
  const [newLang, setNewLang] = useState('')
  const selNodes = nodes.filter((n) => selectedNodeIds.includes(n.id))
  const selEdges = edges.filter((e) => selectedEdgeIds.includes(e.id))
  const total = selNodes.length + selEdges.length

  const orphanedSection = orphanedStates.length > 0 && (
    <div className={styles.orphanedSection}>
      <div className={styles.orphanedHeader}>⚠ Removed states with live data</div>
      <p className={styles.orphanedHint}>
        Saving will set these entities to no state. Re-add before saving to preserve them.
      </p>
      {orphanedStates.map(s => {
        const label = s.label['en'] ?? Object.values(s.label)[0] ?? s.name
        const count = stateCounts[s.name] ?? 0
        return (
          <div key={s.name} className={styles.orphanedItem}>
            <div className={styles.orphanedItemInfo}>
              <span className={styles.orphanedItemName}>{label}</span>
              <span className={styles.orphanedItemCount}>{count} entit{count === 1 ? 'y' : 'ies'}</span>
            </div>
            <button className={styles.readdBtn} onClick={() => onReadd(s)}>Re-add</button>
          </div>
        )
      })}
    </div>
  )

  function confirmAddLang() {
    const trimmed = newLang.trim().toLowerCase()
    if (trimmed && LANG_PATTERN.test(trimmed)) {
      onAddLanguage(trimmed)
    }
    setNewLang('')
    setAddingLang(false)
  }

  const langSection = (
    <div className={styles.propsSection}>
      <div className={styles.propsSectionTitle}>Languages</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
        {languages.map(lang => (
          <span key={lang} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, background: '#e9ecef', borderRadius: 4, padding: '2px 7px', fontSize: '0.78rem', fontFamily: 'monospace' }}>
            {lang}
            {languages.length > 1 && (
              <button
                onClick={() => onRemoveLanguage(lang)}
                style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#888', padding: 0, fontSize: '0.75rem', lineHeight: 1 }}
                title={`Remove language ${lang}`}
              >×</button>
            )}
          </span>
        ))}
      </div>
      {addingLang ? (
        <div style={{ display: 'flex', gap: 4 }}>
          <input
            className={styles.propsInput}
            value={newLang}
            onChange={e => setNewLang(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') confirmAddLang(); if (e.key === 'Escape') { setAddingLang(false); setNewLang('') } }}
            placeholder="e.g. de"
            autoFocus
            style={{ flex: 1 }}
          />
          <button className={styles.deleteBtn} style={{ borderColor: '#198754', color: '#198754' }} onClick={confirmAddLang}>Add</button>
          <button className={styles.deleteBtn} onClick={() => { setAddingLang(false); setNewLang('') }}>✕</button>
        </div>
      ) : (
        <button className={styles.deleteBtn} style={{ borderColor: '#6c757d', color: '#6c757d', fontSize: '0.75rem' }} onClick={() => setAddingLang(true)}>+ Add language</button>
      )}
    </div>
  )

  if (total === 0) {
    return (
      <div className={styles.propsPanel}>
        {langSection}
        {orphanedSection}
        <p className={styles.propsPanelHint}>Select a state or transition to edit its properties.</p>
      </div>
    )
  }

  return (
    <div className={styles.propsPanel}>
      {langSection}
      {orphanedSection}
      <div className={styles.propsPanelHeader}>
        <span>{total} selected</span>
        <button className={styles.deleteBtn} onClick={onDeleteSelected} title="Delete selected">✕ Delete</button>
      </div>

      {selNodes.map((n) => {
        if (n.type !== 'workflowState') {
          return (
            <div key={n.id} className={styles.propsSection}>
              <div className={styles.propsSectionTitle}>
                {n.type === 'fromAny' ? 'From Any' : n.type === 'fromUndefined' ? 'From Undefined' : 'Keep State'}
              </div>
              <p className={styles.specialDesc}>{SPECIAL_DESCRIPTIONS[n.id] ?? ''}</p>
            </div>
          )
        }
        const d = nodeStateData(n)
        const currentStateName = d.name ?? n.id
        const otherStateNames = new Set(
          nodes.filter(x => x.id !== n.id && x.type === 'workflowState')
               .map(x => nodeStateData(x).name ?? x.id)
        )
        const stateNameErr = currentStateName === ''
          ? 'Required'
          : !/^[a-z][a-z0-9_-]*$/.test(currentStateName)
          ? 'Lowercase letters, digits, _ or - only; must start with a letter'
          : otherStateNames.has(currentStateName)
          ? 'Name already used by another state'
          : null
        return (
          <div key={n.id} className={styles.propsSection}>
            <div className={styles.propsSectionTitle}>State: {n.id}</div>
            {languages.map(lang => (
              <label key={lang} className={styles.propsLabel}>
                Label [{lang}]
                <input
                  className={styles.propsInput}
                  value={d.labels?.[lang] ?? ''}
                  onChange={(e) => onNodeChange(n.id, { labels: { ...d.labels, [lang]: e.target.value } })}
                />
              </label>
            ))}
            <label className={styles.propsLabel}>
              Slug
              <input
                className={styles.propsInput}
                value={currentStateName}
                onChange={(e) => onNodeChange(n.id, { name: e.target.value })}
                style={stateNameErr ? { borderColor: '#dc3545' } : undefined}
                title="Rename this state slug (only lowercase letters, digits, _ or -)"
              />
              {stateNameErr && <span style={{ color: '#dc3545', fontSize: '0.75rem' }}>{stateNameErr}</span>}
            </label>
            <label className={styles.propsCheckbox}>
              <input type="checkbox" checked={d.isInitial} onChange={(e) => onNodeChange(n.id, { isInitial: e.target.checked })} />
              Initial state
            </label>
            <label className={styles.propsLabel}>
              Background color
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
                <ColorPicker
                  value={(d.backgroundColor ?? '#ffffff').replace('#', '')}
                  onChange={(e: ColorPickerChangeEvent) => {
                    const raw = typeof e.value === 'string' ? e.value : ''
                    const hex = raw.length === 6 ? `#${raw}` : (d.backgroundColor ?? '#ffffff')
                    onNodeChange(n.id, { backgroundColor: hex, textColor: wcagTextColor(hex) })
                  }}
                  format="hex"
                  style={{ flexShrink: 0 }}
                />
                <input
                  className={styles.propsInput}
                  value={d.backgroundColor ?? '#ffffff'}
                  onChange={(e) => {
                    const val = e.target.value
                    if (/^#[0-9a-fA-F]{6}$/.test(val)) {
                      onNodeChange(n.id, { backgroundColor: val, textColor: wcagTextColor(val) })
                    } else {
                      onNodeChange(n.id, { backgroundColor: val })
                    }
                  }}
                  placeholder="#ffffff"
                  style={{ fontFamily: 'monospace', flex: 1 }}
                  maxLength={7}
                />
              </div>
            </label>
          </div>
        )
      })}

      {selEdges.map((e) => {
        const eData = e.data as WfEdgeData
        const eLabels = eData.labels ?? {}
        const currentTransName = eData.name ?? e.id
        const otherTransNames = new Set(
          edges.filter(x => x.id !== e.id)
               .map(x => (x.data as WfEdgeData).name ?? x.id)
        )
        const transNameErr = currentTransName === ''
          ? 'Required'
          : !/^[a-z][a-z0-9_-]*$/.test(currentTransName)
          ? 'Lowercase letters, digits, _ or - only; must start with a letter'
          : otherTransNames.has(currentTransName)
          ? 'Name already used by another transition'
          : null
        return (
          <div key={e.id} className={styles.propsSection}>
            <div className={styles.propsSectionTitle}>Transition: {e.source} → {e.target}</div>
            {languages.map(lang => (
              <label key={lang} className={styles.propsLabel}>
                Label [{lang}]
                <input
                  className={styles.propsInput}
                  value={eLabels[lang] ?? ''}
                  onChange={(ev) => onEdgeChange(e.id, { labels: { ...eLabels, [lang]: ev.target.value } })}
                />
              </label>
            ))}
            <label className={styles.propsLabel}>
              Slug
              <input
                className={styles.propsInput}
                value={currentTransName}
                onChange={(ev) => onEdgeChange(e.id, { name: ev.target.value })}
                style={transNameErr ? { borderColor: '#dc3545' } : undefined}
                title="Rename this transition slug"
              />
              {transNameErr && <span style={{ color: '#dc3545', fontSize: '0.75rem' }}>{transNameErr}</span>}
            </label>
          </div>
        )
      })}
    </div>
  )
}

// ─── Inner editor (needs ReactFlow context) ───────────────────────────────────

interface EditorInnerProps {
  initialNodes: AnyWfNode[]
  initialEdges: WfEdge[]
  workflowId: string | null
  workflowName: string
  workflowDescription: string
  onSaved: (wf: WorkflowDefinitionOut) => void
  initialStateCounts: Record<string, number>
  savedStates: WorkflowStateOut[]
  reloadGen: number
  hasPublishedVersion: boolean
}

function EditorInner({ initialNodes, initialEdges, workflowId, workflowName: initName, workflowDescription: initDesc, onSaved, initialStateCounts, savedStates, reloadGen, hasPublishedVersion }: EditorInnerProps) {
  const [nodes, setNodes] = useNodesState<AnyWfNode>(initialNodes)
  const [edges, setEdges] = useEdgesState<WfEdge>(initialEdges)
  const [name, setName] = useState(initName)
  const [desc, setDesc] = useState(initDesc)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveSuccess, setSaveSuccess] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [publishError, setPublishError] = useState<string | null>(null)
  const [publishSuccess, setPublishSuccess] = useState(false)
  const [languages, setLanguages] = useState(() => deriveLanguages(initialNodes, initialEdges))
  const { fitView } = useReactFlow()

  useEffect(() => {
    setNodes(initialNodes)
    setEdges(initialEdges)
    setName(initName)
    setDesc(initDesc)
    setLanguages(deriveLanguages(initialNodes, initialEdges))
    setTimeout(() => fitView({ padding: 0.15 }), 50)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, initName, reloadGen])

  // Inject usage counts into workflowState nodes whenever counts arrive/refresh
  useEffect(() => {
    if (Object.keys(initialStateCounts).length === 0) return
    setNodes(nds => nds.map(n =>
      n.type === 'workflowState'
        ? { ...n, data: { ...n.data, usageCount: initialStateCounts[n.id] ?? 0 } }
        : n,
    ))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialStateCounts])

  const selectedNodeIds = nodes.filter((n) => n.selected).map((n) => n.id)
  const selectedEdgeIds = edges.filter((e) => e.selected).map((e) => e.id)

  const isValidConnection = useCallback(
    (connection: WfEdge | Connection) => {
      const src = nodes.find((n) => n.id === connection.source)
      const tgt = nodes.find((n) => n.id === connection.target)
      if (!src || !tgt) return false
      // fromAny / fromUndefined are source-only; reject any incoming edge
      if (tgt.type === 'fromAny' || tgt.type === 'fromUndefined') return false
      // keepState is target-only; reject any outgoing edge
      if (src.type === 'keepState') return false
      return true
    },
    [nodes],
  )

  const onConnect = useCallback(
    (connection: Connection) => {
      const existingNames = new Set(edges.map((e) => e.id))
      const srcNode = nodes.find((n) => n.id === connection.source)
      // Special nodes don't have a meaningful label; fall back to a generic prefix
      const srcLabel = srcNode?.type === 'workflowState'
        ? String(nodeStateData(srcNode).label)
        : srcNode?.type === 'fromAny'   ? 'from_any'
        : srcNode?.type === 'fromUndefined' ? 'from_undef'
        : 't'
      const base = toSlug(srcLabel, 't')
      const slug = uniqueSlug(`t_${base}`, existingNames)
      setEdges((eds) =>
        addEdge({ ...connection, id: slug, data: { label: '', labels: {}, name: slug }, label: '', markerEnd: { type: MarkerType.ArrowClosed }, type: 'smoothstep', reconnectable: true }, eds),
      )
    },
    [edges, nodes, setEdges],
  )

  const onNodesChange = useCallback(
    (changes: NodeChange<AnyWfNode>[]) => setNodes((nds) => applyNodeChanges(changes, nds)),
    [setNodes],
  )

  const onEdgesChange = useCallback(
    (changes: EdgeChange<WfEdge>[]) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    [setEdges],
  )

  const onReconnect = useCallback(
    (oldEdge: WfEdge, newConnection: Connection) =>
      setEdges((eds) => reconnectEdge(oldEdge, newConnection, eds)),
    [setEdges],
  )

  function addStateNode() {
    const existingNames = new Set(nodes.map((n) => n.id))
    const slug = uniqueSlug('state', existingNames)
    const hasInitial = nodes.some((n) => n.type === 'workflowState' && nodeStateData(n).isInitial)
    setNodes((nds) => [
      ...nds,
      {
        id: slug,
        type: 'workflowState',
        position: { x: 80 + nds.filter((n) => n.type === 'workflowState').length * 40, y: 80 },
        data: { label: 'New State', labels: { [languages[0] ?? 'en']: 'New State' }, name: slug, isInitial: !hasInitial, backgroundColor: '#ffffff', textColor: '#000000' } as Record<string, unknown>,
      },
    ])
  }

  function addSpecialNode(type: 'fromAny' | 'fromUndefined' | 'keepState') {
    const id = type === 'fromAny' ? ID_FROM_ANY : type === 'fromUndefined' ? ID_FROM_UNDEFINED : ID_KEEP_STATE
    if (nodes.some((n) => n.id === id)) return
    const xs = nodes.map((n) => n.position.x)
    const ys = nodes.map((n) => n.position.y)
    const midY = ys.length ? (Math.min(...ys) + Math.max(...ys)) / 2 : 100
    const x = type === 'keepState'
      ? (xs.length ? Math.max(...xs) + NODE_WIDTH + 60 : 600)
      : (xs.length ? Math.min(...xs) - 200 : -160)
    const y = type === 'fromUndefined' ? midY + 100 : midY - 30
    setNodes((nds) => [...nds, { id, type, position: { x, y }, data: {} }])
  }

  function handleNodeChange(id: string, patch: Partial<WfStateData>) {
    setNodes((nds) =>
      nds.map((n) => {
        if (n.id !== id || n.type !== 'workflowState') return n
        const newData = { ...(n.data as WfStateData), ...patch }
        if (patch.labels !== undefined) {
          newData.label = pickDisplayLabel(patch.labels, languages[0] ?? 'en', String(newData.name))
        }
        return { ...n, data: newData as Record<string, unknown> }
      }),
    )
    if (patch.isInitial) {
      setNodes((nds) =>
        nds.map((n) =>
          n.id !== id && n.type === 'workflowState' && (n.data as WfStateData).isInitial
            ? { ...n, data: { ...n.data, isInitial: false } }
            : n,
        ),
      )
    }
  }

  function handleEdgeChange(id: string, patch: Partial<WfEdgeData>) {
    setEdges((eds) =>
      eds.map((e) => {
        if (e.id !== id) return e
        const newData = { ...(e.data as WfEdgeData), ...patch }
        if (patch.labels !== undefined) {
          newData.label = pickDisplayLabel(patch.labels, languages[0] ?? 'en', e.id)
        }
        return { ...e, data: newData, label: newData.label }
      }),
    )
  }

  function addLanguage(lang: string) {
    setLanguages(prev => prev.includes(lang) ? prev : [...prev, lang])
  }

  function removeLanguage(lang: string) {
    const newLangs = languages.filter(l => l !== lang)
    setLanguages(newLangs)
    const primary = newLangs[0] ?? 'en'
    setNodes(nds => nds.map(n => {
      if (n.type !== 'workflowState') return n
      const d = n.data as WfStateData
      const newLabels = Object.fromEntries(Object.entries(d.labels ?? {}).filter(([k]) => k !== lang))
      return { ...n, data: { ...n.data, labels: newLabels, label: pickDisplayLabel(newLabels, primary, String(d.name)) } as Record<string, unknown> }
    }))
    setEdges(eds => eds.map(e => {
      const eData = e.data as WfEdgeData
      const newLabels = Object.fromEntries(Object.entries(eData.labels ?? {}).filter(([k]) => k !== lang))
      const displayLabel = pickDisplayLabel(newLabels, primary, e.id)
      return { ...e, data: { ...eData, labels: newLabels, label: displayLabel }, label: displayLabel }
    }))
  }

  function deleteSelected() {
    setNodes((nds) => nds.filter((n) => !n.selected))
    setEdges((eds) => eds.filter((e) => !e.selected))
  }

  // Orphaned = published states not on the draft canvas that still have live entities
  const currentNodeIds = new Set(nodes.map((n) => n.id))
  const orphanedStates = savedStates.filter(
    (s) => !currentNodeIds.has(s.name) && (initialStateCounts[s.name] ?? 0) > 0,
  )

  function readd(state: WorkflowStateOut) {
    const primary = languages[0] ?? 'en'
    const label = state.label[primary] ?? Object.values(state.label)[0] ?? state.name
    const hasInitial = nodes.some((n) => n.type === 'workflowState' && nodeStateData(n).isInitial)
    setNodes((nds) => [
      ...nds,
      {
        id: state.name,
        type: 'workflowState',
        position: { x: state.position_x, y: state.position_y },
        data: {
          label,
          labels: { ...state.label },
          name: state.name,
          isInitial: state.is_initial && !hasInitial,
          usageCount: initialStateCounts[state.name] ?? 0,
          backgroundColor: state.background_color ?? '#ffffff',
          textColor: state.text_color ?? '#000000',
        },
      },
    ])
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    setSaveSuccess(false)
    try {
      const payload = reactFlowToWf(nodes, edges, name, desc, languages[0] ?? 'en')
      const result = workflowId
        ? await updateWorkflow(workflowId, payload)
        : await createWorkflow(payload)
      setSaveSuccess(true)
      onSaved(result)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function handlePublish() {
    if (!workflowId) return
    setPublishing(true)
    setPublishError(null)
    setPublishSuccess(false)
    try {
      const result = await publishWorkflowDraft(workflowId)
      setPublishSuccess(true)
      onSaved(result)
      setTimeout(() => setPublishSuccess(false), 3000)
    } catch (e) {
      setPublishError(e instanceof Error ? e.message : 'Publish failed')
    } finally {
      setPublishing(false)
    }
  }

  const hasFromAny       = nodes.some((n) => n.id === ID_FROM_ANY)
  const hasFromUndefined = nodes.some((n) => n.id === ID_FROM_UNDEFINED)
  const hasKeepState     = nodes.some((n) => n.id === ID_KEEP_STATE)

  return (
    <div className={styles.editorRoot}>
      <div className={styles.toolbar}>
        <input className={styles.nameInput} placeholder="Workflow name" value={name} onChange={(e) => setName(e.target.value)} />
        <input className={styles.descInput} placeholder="Description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
        <span className={styles.toolbarSep} />
        <button className={styles.toolbarBtn} onClick={addStateNode}>+ State</button>
        <button className={`${styles.toolbarBtn} ${styles.fromAnyBtn}`}  onClick={() => addSpecialNode('fromAny')}  disabled={hasFromAny}  title="Add a 'From Any' virtual source node">+ From Any</button>
        <button className={`${styles.toolbarBtn} ${styles.fromUndefBtn}`} onClick={() => addSpecialNode('fromUndefined')} disabled={hasFromUndefined} title="Add a 'From Undefined' virtual source node">+ From Undefined</button>
        <button className={`${styles.toolbarBtn} ${styles.keepStateBtn}`} onClick={() => addSpecialNode('keepState')} disabled={hasKeepState} title="Add a 'Keep State' virtual target node">+ Keep State</button>
        <span className={styles.toolbarSep} />
        <button className={`${styles.toolbarBtn} ${styles.saveBtn}`} onClick={handleSave} disabled={saving || !name.trim()}>
          {saving ? 'Saving…' : workflowId ? 'Save Draft' : 'Create Draft'}
        </button>
        {workflowId && (
          <button
            className={`${styles.toolbarBtn} ${styles.saveBtn}`}
            style={{ background: '#198754', borderColor: '#198754', color: '#fff' }}
            onClick={handlePublish}
            disabled={publishing || !workflowId}
            title="Publish the current draft, making it the active version for entities"
          >
            {publishing ? 'Publishing…' : hasPublishedVersion ? 'Publish Draft' : 'Publish (first)'}
          </button>
        )}
        {saveSuccess && <span className={styles.saveOk}>✓ Saved</span>}
        {saveError   && <span className={styles.saveErr}>{saveError}</span>}
        {publishSuccess && <span className={styles.saveOk}>✓ Published</span>}
        {publishError   && <span className={styles.saveErr}>{publishError}</span>}
      </div>

      <div className={styles.canvasAndPanel}>
        <div className={styles.canvas}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onReconnect={onReconnect}
            isValidConnection={isValidConnection}
            nodeTypes={NODE_TYPES}
            connectionMode={ConnectionMode.Loose}
            defaultEdgeOptions={{ reconnectable: true }}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            deleteKeyCode="Delete"
          >
            <Background />
            <Controls />
            <MiniMap />
            <Panel position="bottom-left">
              <span className={styles.hint}>Drag handles to connect • drag edge endpoints to reconnect • Delete removes selected</span>
            </Panel>
          </ReactFlow>
        </div>
        <PropertiesPanel
          nodes={nodes}
          edges={edges}
          selectedNodeIds={selectedNodeIds}
          selectedEdgeIds={selectedEdgeIds}
          onNodeChange={handleNodeChange}
          onEdgeChange={handleEdgeChange}
          onDeleteSelected={deleteSelected}
          orphanedStates={orphanedStates}
          stateCounts={initialStateCounts}
          onReadd={readd}
          languages={languages}
          onAddLanguage={addLanguage}
          onRemoveLanguage={removeLanguage}
        />
      </div>
    </div>
  )
}

// ─── Outer shell with workflow list / load / examples ─────────────────────────

export function WorkflowEditor() {
  const [workflows, setWorkflows] = useState<WorkflowDefinitionOut[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [activeName, setActiveName] = useState('My Workflow')
  const [activeDesc, setActiveDesc] = useState('')
  const [activeNodes, setActiveNodes] = useState<AnyWfNode[]>([])
  const [activeEdges, setActiveEdges] = useState<WfEdge[]>([])
  const [activeStateCounts, setActiveStateCounts] = useState<Record<string, number>>({})
  const [activeSavedStates, setActiveSavedStates] = useState<WorkflowStateOut[]>([])
  const [activeHasPublished, setActiveHasPublished] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [reloadGen, setReloadGen] = useState(0)
  const loadedRef = useRef(false)

  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true
    listWorkflows().then(setWorkflows).catch((e: Error) => setLoadError(e.message))
  }, [])

  function refreshCounts(id: string) {
    fetchStateCounts(id).then(setActiveStateCounts).catch(() => {})
  }

  function openNew() {
    setActiveId(null)
    setActiveName('New Workflow')
    setActiveDesc('')
    setActiveNodes([])
    setActiveEdges([])
    setActiveStateCounts({})
    setActiveSavedStates([])
    setActiveHasPublished(false)
  }

  function loadWorkflow(wf: WorkflowDefinitionOut) {
    const { nodes, edges } = wfToReactFlow(wf)
    setActiveId(wf.id)
    setActiveName(wf.name)
    setActiveDesc(wf.description)
    setActiveNodes(nodes)
    setActiveEdges(edges)
    setActiveSavedStates(wf.states)
    setActiveStateCounts({})
    setActiveHasPublished(!!wf.published_version_id)
    refreshCounts(wf.id)
  }

  function loadExample() {
    setActiveId(null)
    setActiveName('Proposal Workflow')
    setActiveDesc('Workflow for conference talk proposals (from apiv1/flows.py)')
    setActiveNodes(PROPOSAL_EXAMPLE.nodes)
    setActiveEdges(PROPOSAL_EXAMPLE.edges)
    setActiveStateCounts({})
    setActiveSavedStates([])
  }

  async function handleDelete() {
    if (!activeId) return
    if (!confirm('Delete this workflow?')) return
    setDeleting(true)
    try {
      await deleteWorkflow(activeId)
      setWorkflows((wfs) => wfs.filter((w) => w.id !== activeId))
      openNew()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeleting(false)
    }
  }

  function onSaved(wf: WorkflowDefinitionOut) {
    const { nodes, edges } = wfToReactFlow(wf)
    setActiveId(wf.id)
    setActiveName(wf.name)
    setActiveDesc(wf.description)
    setActiveNodes(nodes)
    setActiveEdges(edges)
    setActiveSavedStates(wf.states)
    setActiveHasPublished(!!wf.published_version_id)
    setWorkflows((wfs) => {
      const idx = wfs.findIndex((w) => w.id === wf.id)
      return idx >= 0 ? wfs.map((w) => (w.id === wf.id ? wf : w)) : [...wfs, wf]
    })
    refreshCounts(wf.id)
    setReloadGen((g) => g + 1)
  }

  return (
    <div className={styles.page}>
      <div className={styles.sidebar}>
        <div className={styles.sidebarHeader}>Workflows</div>
        {loadError && <div className={styles.loadError}>{loadError}</div>}
        <button className={styles.sidebarNewBtn} onClick={openNew}>+ New Workflow</button>
        <div className={styles.sidebarSection}>Examples</div>
        <button className={styles.sidebarItem} onClick={loadExample}>Proposal Workflow</button>
        <div className={styles.sidebarSection}>Saved</div>
        {workflows.length === 0 && <div className={styles.sidebarEmpty}>No saved workflows</div>}
        {workflows.map((wf) => (
          <button
            key={wf.id}
            className={`${styles.sidebarItem} ${wf.id === activeId ? styles.sidebarItemActive : ''}`}
            onClick={() => loadWorkflow(wf)}
            title={wf.published_version_id ? 'Has published version' : 'Draft only — not yet published'}
          >
            {wf.name}
            {!wf.published_version_id && <span style={{ fontSize: '0.65rem', marginLeft: 4, color: '#f97316', fontWeight: 600 }}>DRAFT</span>}
          </button>
        ))}
        {activeId && (
          <button className={styles.deleteWorkflowBtn} onClick={handleDelete} disabled={deleting}>
            {deleting ? 'Deleting…' : 'Delete workflow'}
          </button>
        )}
      </div>

      <div className={styles.editorArea}>
        <ReactFlowProvider>
          <EditorInner
            key={activeId ?? '__new__'}
            initialNodes={activeNodes}
            initialEdges={activeEdges}
            workflowId={activeId}
            workflowName={activeName}
            workflowDescription={activeDesc}
            onSaved={onSaved}
            initialStateCounts={activeStateCounts}
            savedStates={activeSavedStates}
            reloadGen={reloadGen}
            hasPublishedVersion={activeHasPublished}
          />
        </ReactFlowProvider>
      </div>
    </div>
  )
}
