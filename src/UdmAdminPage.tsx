import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Tree } from 'primereact/tree'
import type { TreeNode } from 'primereact/treenode'
import type { TreeDragDropEvent } from 'primereact/tree'
import { UdmApiError } from './apiUdm'
import {
  udmListConfigs,
  udmCreateConfig,
  udmUpdateConfig,
  udmDeleteConfig,
  udmGetDraftVersion,
  udmGetPublishedVersion,
  udmReplaceDraft,
  udmPublishDraft,
  udmListPolicies,
  udmCreatePolicy,
  udmUpdatePolicy,
  udmDeletePolicy,
  udmUpdateType,
  udmListTypePolicies,
  udmAssignPolicy,
  udmRemovePolicy,
  udmListTypes,
  udmCreateType,
  udmSearchEntities,
  udmSearchUsers,
  udmEvalPolicy,
  udmListWorkflows,
  type FieldConfigOut,
  type ConfigVersionOut,
  type FieldDefinitionIn,
  type FieldDefinitionOut,
  type PolicyOut,
  type UDMTypeOut,
  type DataType,
  type PolicyEvalOut,
  type EntityAutocompleteItem,
  type UserAutocompleteItem,
  type WorkflowDefinitionOut,
} from './apiUdm'
import { usePermissions } from './usePermissions'
import { BulkMigrationTab } from './UdmMigration'
import styles from './UdmAdminPage.module.css'

type AdminTab = 'configs' | 'policies' | 'types' | 'migrations'
type ConfigView = 'list' | 'detail'

const DATA_TYPES: DataType[] = [
  'text_short', 'text_long', 'text_markdown', 'text_richtext',
  'integer', 'float', 'boolean', 'date', 'time', 'datetime',
  'select_single', 'select_multi', 'image', 'file',
  'user_select', 'user_select_multi', 'group_select', 'group_select_multi',
  'submodel_select', 'submodel_list', 'entity_select', 'entity_select_multi',
  'slug_id', 'workflow',
]

const STRUCTURAL_TYPES: DataType[] = ['tab_container', 'tab', 'save_button', 'hstack', 'hstack_group', 'tab_prev', 'tab_next']
const STRUCTURAL_SET = new Set<string>(STRUCTURAL_TYPES)
// Types that can have child fields via parent_slug
const PARENT_TYPES = new Set<string>(['tab_container', 'tab', 'hstack', 'hstack_group'])

// ── Helpers ───────────────────────────────────────────────────────────────────

function statusBadge(status: string) {
  const cls = status === 'draft' ? styles.badgeDraft
    : status === 'published' ? styles.badgePublished
    : styles.badgeArchived
  return <span className={`${styles.badge} ${cls}`}>{status}</span>
}

// ── Field Definition Editor ───────────────────────────────────────────────────

// ── Submodel version picker ───────────────────────────────────────────────────

interface SubmodelVersionPickerProps {
  value: string | null | undefined
  onChange: (versionId: string | null) => void
  allConfigs: FieldConfigOut[]
}

function SubmodelVersionPicker({ value, onChange, allConfigs }: SubmodelVersionPickerProps) {
  const [selectedConfigId, setSelectedConfigId] = useState<string>('')
  const [versions, setVersions] = useState<import('./apiUdm').ConfigVersionListItem[]>([])
  const [loadingVersions, setLoadingVersions] = useState(false)

  // When a config is chosen, load its versions
  useEffect(() => {
    if (!selectedConfigId) { setVersions([]); return }
    setLoadingVersions(true)
    import('./apiUdm').then(({ udmListConfigVersions }) =>
      udmListConfigVersions(selectedConfigId)
        .then(setVersions)
        .catch(() => setVersions([]))
        .finally(() => setLoadingVersions(false))
    )
  }, [selectedConfigId])

  // If we have a current value but no selectedConfigId, try to derive the config
  // by finding which config owns the version (via published/draft lookup — best effort)
  // — we just leave the picker blank and show the current ID as info text.

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
      <label className={styles.label}>Config (for submodel version)</label>
      <select className={styles.select} value={selectedConfigId}
        onChange={e => { setSelectedConfigId(e.target.value); onChange(null) }}>
        <option value="">— select config —</option>
        {allConfigs.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>

      {selectedConfigId && (
        <>
          <label className={styles.label}>Config Version *</label>
          {loadingVersions ? (
            <div style={{ fontSize: '0.82rem', color: '#888' }}>Loading versions…</div>
          ) : (
            <select className={styles.select} value={value ?? ''}
              onChange={e => onChange(e.target.value || null)}>
              <option value="">— select version —</option>
              {versions.map(v => (
                <option key={v.id} value={v.id}>
                  {v.status} {v.published_at ? `(published ${new Date(v.published_at).toLocaleDateString()})` : `(created ${new Date(v.created_at).toLocaleDateString()})`}
                </option>
              ))}
            </select>
          )}
        </>
      )}

      {value && (
        <div style={{ fontSize: '0.78rem', color: '#666' }}>
          Current version ID: <span className={styles.monoText}>{value}</span>
          {' '}<button type="button" style={{ fontSize: '0.78rem', color: '#dc2626', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
            onClick={() => onChange(null)}>clear</button>
        </div>
      )}
    </div>
  )
}

// ── Workflow Definition Picker ────────────────────────────────────────────────

function WorkflowDefinitionPicker({ value, onChange }: { value: string | null; onChange: (id: string | null) => void }) {
  const [workflows, setWorkflows] = useState<WorkflowDefinitionOut[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    udmListWorkflows().then(setWorkflows).catch(() => setWorkflows([])).finally(() => setLoading(false))
  }, [])

  return (
    <div className={styles.formGroup} style={{ gridColumn: '1 / -1' }}>
      <label className={styles.label}>Workflow Definition * {loading && '(loading…)'}</label>
      <select
        className={styles.select}
        value={value ?? ''}
        onChange={e => onChange(e.target.value || null)}
      >
        <option value="">— select a workflow —</option>
        {workflows.map(wf => (
          <option key={wf.id} value={wf.id}>
            {wf.name} ({wf.states.length} states, {wf.transitions.length} transitions)
          </option>
        ))}
      </select>
      {workflows.length === 0 && !loading && (
        <div style={{ fontSize: '0.8rem', color: '#888', marginTop: '0.25rem' }}>
          No workflows found. Create one in the Workflow Editor first.
        </div>
      )}
    </div>
  )
}

// ── Field Definition Editor ───────────────────────────────────────────────────

interface FieldEditorProps {
  field: FieldDefinitionIn
  onChange: (f: FieldDefinitionIn) => void
  onRemove: () => void
  languages: string[]
  allConfigs: FieldConfigOut[]
  /** When true, skip the card header (used when embedded inside TreeItemRow) */
  noHeader?: boolean
}

// Types that cannot have manual defaults (mirrors backend _NO_DEFAULT_TYPES + slug_id is auto)
const NO_DEFAULT_TYPES = new Set<DataType>([
  'image', 'file', 'entity_select', 'entity_select_multi',
  'submodel_select', 'submodel_list', 'workflow',
  ...(STRUCTURAL_TYPES as DataType[]),
])

// FK-based types where defaults require a live lookup — deferred to a future picker
const FK_DEFAULT_TYPES = new Set<DataType>([
  'user_select', 'user_select_multi', 'group_select', 'group_select_multi',
])

interface DefaultValueEditorProps {
  dt: DataType
  tc: Record<string, unknown>
  value: unknown
  isLocalized: boolean
  languages: string[]
  onChange: (v: unknown) => void
}

function DefaultValueEditor({ dt, tc, value, isLocalized, languages, onChange }: DefaultValueEditorProps) {
  if (dt === 'slug_id') {
    return (
      <div style={{ fontSize: '0.85rem', color: '#666', fontStyle: 'italic' }}>
        Auto-generated sequential ID (no manual default)
      </div>
    )
  }
  if (NO_DEFAULT_TYPES.has(dt)) return null
  if (FK_DEFAULT_TYPES.has(dt)) {
    return (
      <div style={{ fontSize: '0.85rem', color: '#999', fontStyle: 'italic' }}>
        Defaults for this type require a live lookup — set via entity creation instead.
      </div>
    )
  }

  const renderInput = (val: unknown, onChangeVal: (v: unknown) => void, key?: string) => {
    const strVal = val != null ? String(val) : ''
    if (dt === 'boolean') {
      return (
        <label className={styles.checkbox} key={key}>
          <input type="checkbox" checked={!!val}
            onChange={e => onChangeVal(e.target.checked)} />
          Yes
        </label>
      )
    }
    if (dt === 'integer') {
      return (
        <input key={key} className={styles.input} type="number" step="1" value={strVal}
          onChange={e => onChangeVal(e.target.value !== '' ? parseInt(e.target.value) : null)} />
      )
    }
    if (dt === 'float') {
      return (
        <input key={key} className={styles.input} type="number" step="any" value={strVal}
          onChange={e => onChangeVal(e.target.value !== '' ? parseFloat(e.target.value) : null)} />
      )
    }
    if (dt === 'date') {
      return (
        <input key={key} className={styles.input} type="date" value={strVal}
          onChange={e => onChangeVal(e.target.value || null)} />
      )
    }
    if (dt === 'time') {
      return (
        <input key={key} className={styles.input} type="time" value={strVal}
          onChange={e => onChangeVal(e.target.value || null)} />
      )
    }
    if (dt === 'datetime') {
      return (
        <input key={key} className={styles.input} type="datetime-local" value={strVal}
          onChange={e => onChangeVal(e.target.value || null)} />
      )
    }
    if (dt === 'select_single') {
      const choices = (tc['choices'] as string[] | undefined) ?? []
      return (
        <select key={key} className={styles.select} value={strVal}
          onChange={e => onChangeVal(e.target.value || null)}>
          <option value="">— no default —</option>
          {choices.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      )
    }
    if (dt === 'select_multi') {
      const choices = (tc['choices'] as string[] | undefined) ?? []
      const selected = Array.isArray(val) ? (val as string[]) : []
      return (
        <div key={key} style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
          {choices.map(c => (
            <label key={c} className={styles.checkbox}>
              <input type="checkbox" checked={selected.includes(c)}
                onChange={e => {
                  const next = e.target.checked
                    ? [...selected, c]
                    : selected.filter(x => x !== c)
                  onChangeVal(next.length ? next : null)
                }} />
              {c}
            </label>
          ))}
        </div>
      )
    }
    // text_short, text_long, text_markdown, text_richtext
    return (
      <input key={key} className={styles.input} type="text" value={strVal}
        onChange={e => onChangeVal(e.target.value || null)} />
    )
  }

  if (isLocalized) {
    const locVal = (typeof value === 'object' && value !== null && !Array.isArray(value))
      ? value as Record<string, unknown>
      : {}
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
        {languages.map(lang => (
          <div key={lang} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ minWidth: '3rem', fontSize: '0.8rem', color: '#666' }}>[{lang}]</span>
            {renderInput(locVal[lang] ?? null, v => {
              const next = { ...locVal, [lang]: v }
              onChange(Object.values(next).some(x => x != null) ? next : null)
            }, lang)}
          </div>
        ))}
      </div>
    )
  }

  return renderInput(value, onChange)
}

function FieldEditor({ field, onChange, onRemove, languages, allConfigs, noHeader }: FieldEditorProps) {
  const [expanded, setExpanded] = useState(noHeader ?? false)
  const [choicesText, setChoicesText] = useState<string | null>(null)

  const setF = (updates: Partial<FieldDefinitionIn>) => onChange({ ...field, ...updates })
  const setLabel = (lang: string, val: string) =>
    setF({ labels: { ...field.labels, [lang]: val } })
  const setHelpText = (lang: string, val: string) =>
    setF({ help_texts: { ...(field.help_texts ?? {}), [lang]: val } })

  const tc = field.type_config ?? {}

  return (
    <div className={noHeader ? undefined : styles.fieldCard}>
      {!noHeader && (
        <div className={styles.fieldCardHeader}>
          <span className={styles.fieldCardTitle}>
            {field.slug || <em style={{ color: '#999' }}>new field</em>}
            {' '}
            <span className={styles.badge} style={{ background: '#eee', color: '#555', fontSize: '0.75rem' }}>
              {field.data_type}
            </span>
          </span>
          <div className={styles.tableActions}>
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={() => setExpanded(!expanded)}>
              {expanded ? 'Collapse' : 'Edit'}
            </button>
            <button type="button" className={`${styles.btn} ${styles.btnDanger}`} onClick={onRemove}>
              Remove
            </button>
          </div>
        </div>
      )}

      {(noHeader || expanded) && (
        <>
          <div className={styles.fieldCardBody}>
            <div className={styles.formGroup}>
              <label className={styles.label}>Slug *</label>
              <input className={styles.input} value={field.slug}
                onChange={e => setF({ slug: e.target.value })} placeholder="field_slug" />
            </div>
            <div className={styles.formGroup}>
              <label className={styles.label}>Data Type *</label>
              <select className={styles.select} value={field.data_type}
                onChange={e => {
                  const dt = e.target.value as DataType
                  const isSubmodel = dt === 'submodel_select' || dt === 'submodel_list'
                  setF({
                    data_type: dt,
                    submodel_config_version_id: isSubmodel ? field.submodel_config_version_id : null,
                    workflow_definition_id: dt === 'workflow' ? (field.workflow_definition_id ?? null) : null,
                  })
                }}>
                {DATA_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div className={styles.formGroup}>
              <label className={styles.label}>Sort Order</label>
              <input className={styles.input} type="number" value={field.sort_order}
                onChange={e => setF({ sort_order: parseInt(e.target.value) || 0 })} />
            </div>
            <div className={styles.formGroup}>
              <label className={styles.label}>Flags</label>
              <label className={styles.checkbox}>
                <input type="checkbox" checked={field.is_localized}
                  onChange={e => setF({ is_localized: e.target.checked })} />
                Localized
              </label>
              <label className={styles.checkbox}>
                <input type="checkbox" checked={field.is_preview ?? false}
                  onChange={e => setF({ is_preview: e.target.checked })} />
                Preview (shown in collapsed submodel cards and entity dropdowns)
              </label>
            </div>

            {languages.map(lang => (
              <div key={lang} className={styles.formGroup} style={{ minWidth: '200px' }}>
                <label className={styles.label}>Label [{lang}] *</label>
                <input className={styles.input} value={(field.labels ?? {})[lang] ?? ''}
                  onChange={e => setLabel(lang, e.target.value)}
                  placeholder={`Label in ${lang}`} />
                <label className={styles.label} style={{ marginTop: '0.25rem' }}>Help [{lang}]</label>
                <input className={styles.input} value={(field.help_texts ?? {})[lang] ?? ''}
                  onChange={e => setHelpText(lang, e.target.value)}
                  placeholder={`Help text in ${lang}`} />
              </div>
            ))}

            {/* Type config: choices for select */}
            {(field.data_type === 'select_single' || field.data_type === 'select_multi') && (
              <div className={styles.formGroup} style={{ gridColumn: '1 / -1' }}>
                <label className={styles.label}>Choices (one per line) *</label>
                <textarea className={styles.textarea} rows={3}
                  value={choicesText ?? (tc['choices'] as string[] || []).join('\n')}
                  onChange={e => {
                    setChoicesText(e.target.value)
                    setF({
                      type_config: {
                        ...tc,
                        choices: e.target.value.split('\n').map(s => s.trim()).filter(Boolean),
                      },
                    })
                  }}
                  onBlur={() => setChoicesText(null)} />
              </div>
            )}

            {/* Type config: decimal places for float */}
            {field.data_type === 'float' && (
              <div className={styles.formGroup}>
                <label className={styles.label}>Decimal Places</label>
                <input className={styles.input} type="number"
                  value={(tc['decimal_places'] as number) ?? ''}
                  onChange={e => setF({ type_config: { ...tc, decimal_places: parseInt(e.target.value) || undefined } })} />
              </div>
            )}

            {/* Type config: group/user restrictions */}
            {(field.data_type === 'user_select' || field.data_type === 'user_select_multi') && (
              <div className={styles.formGroup} style={{ gridColumn: '1 / -1' }}>
                <label className={styles.label}>Limit to Group IDs (comma-separated, optional)</label>
                <input className={styles.input}
                  value={((tc['limit_to_group_ids'] as number[]) ?? []).join(', ')}
                  onChange={e => setF({
                    type_config: {
                      ...tc,
                      limit_to_group_ids: e.target.value
                        ? e.target.value.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n))
                        : undefined,
                    },
                  })}
                  placeholder="3, 7 (leave blank for all users)" />
              </div>
            )}

            {(field.data_type === 'entity_select' || field.data_type === 'entity_select_multi') && (
              <div className={styles.formGroup} style={{ gridColumn: '1 / -1' }}>
                <label className={styles.label}>Limit to Type IDs (comma-separated, optional)</label>
                <input className={styles.input}
                  value={((tc['limit_to_type_ids'] as string[]) ?? []).join(', ')}
                  onChange={e => setF({
                    type_config: {
                      ...tc,
                      limit_to_type_ids: e.target.value
                        ? e.target.value.split(',').map(s => s.trim()).filter(Boolean)
                        : undefined,
                    },
                  })}
                  placeholder="uuid1, uuid2 (leave blank for any type)" />
              </div>
            )}

            {/* Type config: prefix for slug_id */}
            {field.data_type === 'slug_id' && (
              <div className={styles.formGroup} style={{ gridColumn: '1 / -1' }}>
                <label className={styles.label}>Prefix * (uppercase letters/digits/underscores, globally unique)</label>
                <input className={styles.input}
                  value={(tc['prefix'] as string) ?? ''}
                  onChange={e => setF({ type_config: { ...tc, prefix: e.target.value.toUpperCase() } })}
                  placeholder="TRIMESTERCALL1" />
                <div style={{ fontSize: '0.8rem', color: '#666', marginTop: '0.25rem' }}>
                  Values will display as <code>{(tc['prefix'] as string) || 'PREFIX'}-1234</code>. Auto-generated on entity creation; read-only.
                </div>
              </div>
            )}

            {/* Submodel config version — required for submodel_select / submodel_list */}
            {(field.data_type === 'submodel_select' || field.data_type === 'submodel_list') && (
              <div className={styles.formGroup} style={{ gridColumn: '1 / -1' }}>
                <SubmodelVersionPicker
                  value={field.submodel_config_version_id ?? null}
                  onChange={versionId => setF({ submodel_config_version_id: versionId })}
                  allConfigs={allConfigs}
                />
              </div>
            )}

            {/* Workflow definition — required for workflow type */}
            {field.data_type === 'workflow' && (
              <WorkflowDefinitionPicker
                value={field.workflow_definition_id ?? null}
                onChange={id => setF({ workflow_definition_id: id })}
              />
            )}
          </div>

          {/* Default value */}
          {!NO_DEFAULT_TYPES.has(field.data_type) && (
            <div className={styles.subsection}>
              <span className={styles.subsectionTitle}>Default Value</span>
              <div style={{ marginTop: '0.4rem' }}>
                <DefaultValueEditor
                  dt={field.data_type}
                  tc={tc}
                  value={field.default ?? null}
                  isLocalized={field.is_localized}
                  languages={languages}
                  onChange={v => setF({ default: v })}
                />
              </div>
            </div>
          )}

        </>
      )}
    </div>
  )
}

// ── Draft Editor ──────────────────────────────────────────────────────────────

interface DraftEditorProps {
  configId: string
  languages: string[]
  onSaved: (v: ConfigVersionOut) => void
  allConfigs: FieldConfigOut[]
}

// ── Tree helpers (single source of truth: TreeNode[]) ─────────────────────────

let _uid = 0
function genKey() { return `k-${++_uid}` }

type NodeData = { field: FieldDefinitionIn }

// Convert flat field list (from API) to PrimeReact TreeNode[]
function fieldsToNodes(fields: FieldDefinitionIn[]): TreeNode[] {
  const sorted = [...fields].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
  const childMap = new Map<string, FieldDefinitionIn[]>()
  const roots: FieldDefinitionIn[] = []

  for (const f of sorted) {
    if (f.parent_slug) {
      ;(childMap.get(f.parent_slug) ?? (childMap.set(f.parent_slug, []), childMap.get(f.parent_slug)!)).push(f)
    } else {
      roots.push(f)
    }
  }

  function toNode(f: FieldDefinitionIn): TreeNode {
    const children = (childMap.get(f.slug) ?? []).map(toNode)
    const canHaveChildren = PARENT_TYPES.has(f.data_type)
    return {
      key: genKey(),
      label: f.slug || '(new)',
      data: { field: f } satisfies NodeData,
      children: children.length > 0 ? children : (canHaveChildren ? [] : undefined),
      leaf: !canHaveChildren || children.length === 0,
      draggable: f.data_type !== 'tab_container',
      droppable: canHaveChildren,
    }
  }

  return roots.map(toNode)
}

// Convert TreeNode[] back to flat field list for API, deriving parent_slug and sort_order from tree position
function nodesToFields(nodes: TreeNode[]): FieldDefinitionIn[] {
  const out: FieldDefinitionIn[] = []
  let so = 0

  function walk(list: TreeNode[], parentSlug: string | null) {
    for (const node of list) {
      const field = (node.data as NodeData).field
      out.push({ ...field, sort_order: so++, parent_slug: parentSlug })
      if (node.children && node.children.length > 0) {
        walk(node.children, field.slug)
      }
    }
  }

  walk(nodes, null)
  return out
}

// Recursively update a node by key
function updateNodeByKey(nodes: TreeNode[], key: string, updatedField: FieldDefinitionIn): TreeNode[] {
  return nodes.map(n => {
    if (n.key === key) {
      return {
        ...n,
        label: updatedField.slug || '(new)',
        data: { field: updatedField },
        // When slug changes, we need to re-check droppable/leaf
        droppable: PARENT_TYPES.has(updatedField.data_type),
        leaf: !PARENT_TYPES.has(updatedField.data_type) || (n.children?.length ?? 0) === 0,
      }
    }
    if (n.children) return { ...n, children: updateNodeByKey(n.children, key, updatedField) }
    return n
  })
}

// Recursively remove a node by key
function removeNodeByKey(nodes: TreeNode[], key: string): TreeNode[] {
  return nodes
    .filter(n => n.key !== key)
    .map(n => n.children ? { ...n, children: removeNodeByKey(n.children, key) } : n)
}


// Find expanded keys for structural container nodes
function getInitialExpanded(nodes: TreeNode[]): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  function walk(list: TreeNode[]) {
    for (const n of list) {
      const f = (n.data as NodeData)?.field
      if (f && PARENT_TYPES.has(f.data_type)) out[n.key as string] = true
      if (n.children) walk(n.children)
    }
  }
  walk(nodes)
  return out
}

function makeNewDataField(languages: string[]): FieldDefinitionIn {
  const labels: Record<string, string> = {}
  languages.forEach(l => { labels[l] = '' })
  return { slug: '', data_type: 'text_short', sort_order: 0, is_localized: false, is_preview: false, labels, type_config: {}, default: null }
}

// Auto-generate unique slugs for structural fields based on existing nodes
function makeStructuralField(dt: DataType, nodes: TreeNode[]): FieldDefinitionIn {
  const allFields = nodesToFields(nodes)
  const usedSlugs = new Set(allFields.map(f => f.slug))
  const base = dt.replace(/_/g, '-')
  let slug = base
  let n = 1
  while (usedSlugs.has(slug)) { slug = `${base}-${++n}` }
  return { slug, data_type: dt, sort_order: 0, is_localized: false, is_preview: false, labels: null, type_config: {}, default: null }
}

// ── Structural field editor ────────────────────────────────────────────────────

interface StructuralFieldEditorProps {
  field: FieldDefinitionIn
  onChange: (f: FieldDefinitionIn) => void
}

function StructuralFieldEditor({ field, onChange }: StructuralFieldEditorProps) {
  const tc = field.type_config ?? {}
  const setTc = (updates: Record<string, unknown>) => onChange({ ...field, type_config: { ...tc, ...updates } })

  const slugRow = (
    <div>
      <label className={styles.label}>Slug</label>
      <input className={styles.input} value={field.slug} onChange={e => onChange({ ...field, slug: e.target.value })} placeholder={field.data_type} />
    </div>
  )

  if (field.data_type === 'tab_container') {
    return (
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {slugRow}
        <div style={{ flex: 2 }}>
          <label className={styles.label}>Title (optional)</label>
          <input className={styles.input} value={(tc['title'] as string) ?? ''} onChange={e => setTc({ title: e.target.value })} placeholder="e.g. Form Sections" />
        </div>
      </div>
    )
  }

  if (field.data_type === 'tab') {
    return (
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {slugRow}
        <div style={{ flex: 2 }}>
          <label className={styles.label}>Tab Title *</label>
          <input className={styles.input} value={(tc['title'] as string) ?? ''} onChange={e => setTc({ title: e.target.value })} placeholder="e.g. General" />
        </div>
      </div>
    )
  }

  if (field.data_type === 'save_button') {
    return (
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {slugRow}
        <div style={{ flex: 2 }}>
          <label className={styles.label}>Label</label>
          <input className={styles.input} value={(tc['label'] as string) ?? ''} onChange={e => setTc({ label: e.target.value })} placeholder="Save" />
        </div>
        <div>
          <label className={styles.label}>Variant</label>
          <select className={styles.select} value={(tc['variant'] as string) ?? 'primary'}
            onChange={e => setTc({ variant: e.target.value })}>
            <option value="primary">Primary</option>
            <option value="success">Success</option>
          </select>
        </div>
      </div>
    )
  }

  if (field.data_type === 'hstack') {
    return (
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {slugRow}
        <div style={{ color: '#888', fontSize: '0.78rem', alignSelf: 'flex-end' }}>Add hstack_group children via tree</div>
      </div>
    )
  }

  if (field.data_type === 'hstack_group') {
    return (
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {slugRow}
        <div>
          <label className={styles.label}>Alignment</label>
          <select className={styles.select} value={(tc['align'] as string) ?? 'left'}
            onChange={e => setTc({ align: e.target.value })}>
            <option value="left">Left</option>
            <option value="center">Center</option>
            <option value="right">Right</option>
          </select>
        </div>
      </div>
    )
  }

  if (field.data_type === 'tab_prev' || field.data_type === 'tab_next') {
    return (
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {slugRow}
        <div style={{ flex: 2 }}>
          <label className={styles.label}>Label (optional)</label>
          <input className={styles.input} value={(tc['label'] as string) ?? ''} onChange={e => setTc({ label: e.target.value })}
            placeholder={field.data_type === 'tab_prev' ? '← Previous' : 'Next →'} />
        </div>
      </div>
    )
  }

  return null
}

// ── DraftEditor ───────────────────────────────────────────────────────────────

function DraftEditor({ configId, languages, onSaved, allConfigs }: DraftEditorProps) {
  const [draft, setDraft] = useState<ConfigVersionOut | null>(null)
  const [notes, setNotes] = useState('')
  const [nodes, setNodes] = useState<TreeNode[]>([])
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({})
  const [selectedKeys, setSelectedKeys] = useState<Record<string, boolean>>({})
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [success, setSuccess] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const v = await udmGetDraftVersion(configId)
      setDraft(v)
      setNotes(v.notes)
      const ns = fieldsToNodes(v.fields.map(fdToIn))
      setNodes(ns)
      setExpandedKeys(getInitialExpanded(ns))
    } catch {
      setDraft(null)
      setNodes([])
    }
  }, [configId])

  useEffect(() => { void load() }, [load])

  function fdToIn(fd: FieldDefinitionOut): FieldDefinitionIn {
    const isStructural = STRUCTURAL_SET.has(fd.data_type)
    return {
      slug: fd.slug,
      data_type: fd.data_type as DataType,
      sort_order: fd.sort_order,
      is_localized: fd.is_localized,
      is_preview: fd.is_preview,
      labels: isStructural ? null : (fd.label as Record<string, string>),
      help_texts: fd.help_text as Record<string, string>,
      type_config: fd.type_config as Record<string, unknown>,
      default: fd.default ?? null,
      submodel_config_version_id: fd.submodel_config?.version_id ?? null,
      workflow_definition_id: (fd as FieldDefinitionOut & { workflow_definition?: { id?: string } }).workflow_definition?.id ?? null,
      parent_slug: fd.parent_slug ?? null,
    }
  }

  // ── Mutations (all operate on TreeNode[]) ─────────────────────────────────────

  function addField(parentKey?: string) {
    const field = makeNewDataField(languages)
    addNode(field, parentKey)
  }

  function addStructural(dt: DataType, parentKey?: string) {
    const field = makeStructuralField(dt, nodes)
    addNode(field, parentKey)
  }

  function addNode(field: FieldDefinitionIn, parentKey?: string) {
    const newNode: TreeNode = {
      key: genKey(),
      label: field.slug || '(new)',
      data: { field } satisfies NodeData,
      leaf: !PARENT_TYPES.has(field.data_type),
      draggable: field.data_type !== 'tab_container',
      droppable: PARENT_TYPES.has(field.data_type),
      children: PARENT_TYPES.has(field.data_type) ? [] : undefined,
    }

    if (!parentKey) {
      setNodes(prev => [...prev, newNode])
    } else {
      setNodes(prev => insertUnderKey(prev, parentKey, newNode))
      setExpandedKeys(prev => ({ ...prev, [parentKey]: true }))
    }
  }

  function insertUnderKey(nodeList: TreeNode[], parentKey: string, newNode: TreeNode): TreeNode[] {
    return nodeList.map(n => {
      if (n.key === parentKey) {
        const children = [...(n.children ?? []), newNode]
        return { ...n, children, leaf: false }
      }
      if (n.children) return { ...n, children: insertUnderKey(n.children, parentKey, newNode) }
      return n
    })
  }

  function handleDragDrop(e: TreeDragDropEvent) {
    setNodes(e.value as TreeNode[])
  }

  function deleteSelected() {
    let ns = nodes
    for (const key of Object.keys(selectedKeys)) {
      ns = removeNodeByKey(ns, key)
    }
    setNodes(ns)
    setSelectedKeys({})
    if (editingKey && selectedKeys[editingKey]) setEditingKey(null)
  }

  async function handleSave() {
    setSaving(true)
    setErrors([])
    setSuccess(null)
    try {
      const fields = nodesToFields(nodes)
      const v = await udmReplaceDraft(configId, { notes, fields })
      setDraft(v)
      const ns = fieldsToNodes(v.fields.map(fdToIn))
      setNodes(ns)
      setExpandedKeys(prev => ({ ...getInitialExpanded(ns), ...prev }))
      setSuccess('Draft saved.')
      onSaved(v)
    } catch (e) {
      setErrors(e instanceof UdmApiError ? e.allMessages : [e instanceof Error ? e.message : 'Save failed'])
    } finally {
      setSaving(false)
    }
  }

  async function handlePublish() {
    setPublishing(true)
    setErrors([])
    setSuccess(null)
    try {
      const v = await udmPublishDraft(configId)
      setDraft(v)
      setSuccess('Published successfully.')
      onSaved(v)
    } catch (e) {
      setErrors(e instanceof UdmApiError ? e.allMessages : [e instanceof Error ? e.message : 'Publish failed'])
    } finally {
      setPublishing(false)
    }
  }

  // ── Node template ──────────────────────────────────────────────────────────────

  function nodeTemplate(node: TreeNode) {
    const field = (node.data as NodeData)?.field
    if (!field) return <span>{node.label}</span>

    const isStructural = STRUCTURAL_SET.has(field.data_type)
    const isEditing = editingKey === node.key
    const typeColors: Record<string, { bg: string; color: string }> = {
      tab_container: { bg: '#dbeafe', color: '#1e40af' },
      tab: { bg: '#e0f2fe', color: '#075985' },
      hstack: { bg: '#fef9c3', color: '#854d0e' },
      hstack_group: { bg: '#fef3c7', color: '#92400e' },
      save_button: { bg: '#dcfce7', color: '#166534' },
      tab_prev: { bg: '#f3e8ff', color: '#6b21a8' },
      tab_next: { bg: '#f3e8ff', color: '#6b21a8' },
    }
    const colors = typeColors[field.data_type] ?? { bg: '#f3f4f6', color: '#374151' }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
        {/* Compact row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flex: 1 }}>
          <span style={{
            background: colors.bg, color: colors.color,
            fontSize: '0.65rem', fontWeight: 600, padding: '0.1rem 0.4rem', borderRadius: '3px', flexShrink: 0,
          }}>
            {field.data_type}
          </span>
          <span style={{ fontWeight: 500, fontSize: '0.85rem', fontFamily: isStructural ? 'inherit' : 'monospace', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {field.slug || <em style={{ color: '#bbb', fontStyle: 'italic' }}>new</em>}
            {isStructural && (field.type_config as { title?: string })?.title
              ? <span style={{ fontFamily: 'inherit', fontWeight: 400, color: '#666', marginLeft: '0.4rem' }}>
                  {(field.type_config as { title?: string }).title}
                </span>
              : null}
          </span>
          <div style={{ display: 'flex', gap: '0.2rem', flexShrink: 0 }}>
            {/* Context-aware "Add child" buttons */}
            {field.data_type === 'tab_container' && (
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }} title="Add Tab"
                onClick={e => { e.stopPropagation(); addStructural('tab', node.key as string) }}>+ Tab</button>
            )}
            {field.data_type === 'tab' && (
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }} title="Add Field"
                onClick={e => { e.stopPropagation(); addField(node.key as string) }}>+ Field</button>
            )}
            {field.data_type === 'hstack' && (
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }} title="Add HStack Group"
                onClick={e => { e.stopPropagation(); addStructural('hstack_group', node.key as string) }}>+ Group</button>
            )}
            {field.data_type === 'hstack_group' && (
              <>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                  style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }} title="Add Save Button"
                  onClick={e => { e.stopPropagation(); addStructural('save_button', node.key as string) }}>+ Save</button>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                  style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }} title="Add Previous Tab Button"
                  onClick={e => { e.stopPropagation(); addStructural('tab_prev', node.key as string) }}>+ Prev</button>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                  style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }} title="Add Next Tab Button"
                  onClick={e => { e.stopPropagation(); addStructural('tab_next', node.key as string) }}>+ Next</button>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                  style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }} title="Add Field"
                  onClick={e => { e.stopPropagation(); addField(node.key as string) }}>+ Field</button>
              </>
            )}
            <button
              type="button"
              className={`${styles.btn} ${styles.btnSecondary}`}
              style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }}
              onClick={e => { e.stopPropagation(); setEditingKey(isEditing ? null : node.key as string) }}
            >
              {isEditing ? '✕' : '✎'}
            </button>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnDanger}`}
              style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }}
              onClick={e => { e.stopPropagation(); setNodes(prev => removeNodeByKey(prev, node.key as string)) }}
            >✕</button>
          </div>
        </div>

        {/* Inline editor */}
        {isEditing && (
          <div style={{ marginTop: '0.4rem', padding: '0.6rem', background: '#f8fafc', borderRadius: '4px', border: '1px solid #e2e8f0' }}
            onClick={e => e.stopPropagation()}>
            {isStructural
              ? <StructuralFieldEditor
                  field={field}
                  onChange={updated => setNodes(prev => updateNodeByKey(prev, node.key as string, updated))}
                />
              : <FieldEditor
                  field={field}
                  onChange={updated => setNodes(prev => updateNodeByKey(prev, node.key as string, updated))}
                  onRemove={() => setNodes(prev => removeNodeByKey(prev, node.key as string))}
                  languages={languages}
                  allConfigs={allConfigs}
                  noHeader
                />
            }
          </div>
        )}
      </div>
    )
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  const selectedCount = Object.keys(selectedKeys).length
  const hasTabContainer = nodes.some(n => (n.data as NodeData)?.field?.data_type === 'tab_container')
  const totalCount = nodesToFields(nodes).length

  return (
    <div>
      <div className={styles.subsectionTitle}>Draft Version</div>
      {draft ? (
        <div>
          <div className={styles.row}>
            <div className={styles.formGroup} style={{ flex: 2 }}>
              <label className={styles.label}>Change Notes</label>
              <input className={styles.input} value={notes}
                onChange={e => setNotes(e.target.value)} placeholder="Notes for this version" />
            </div>
          </div>

          <div style={{ marginTop: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem', flexWrap: 'wrap', gap: '0.5rem' }}>
              <span className={styles.subsectionTitle}>Fields ({totalCount})</span>
              <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} style={{ fontSize: '0.78rem' }}
                  onClick={() => addField()}>+ Field</button>
                {!hasTabContainer && (
                  <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} style={{ fontSize: '0.78rem' }}
                    onClick={() => addStructural('tab_container')}>+ Tab Container</button>
                )}
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} style={{ fontSize: '0.78rem' }}
                  onClick={() => addStructural('save_button')}>+ Save Button</button>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} style={{ fontSize: '0.78rem' }}
                  onClick={() => addStructural('tab_prev')}>+ Tab Prev</button>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} style={{ fontSize: '0.78rem' }}
                  onClick={() => addStructural('tab_next')}>+ Tab Next</button>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} style={{ fontSize: '0.78rem' }}
                  onClick={() => addStructural('hstack')}>+ HStack</button>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} style={{ fontSize: '0.78rem' }}
                  onClick={() => addStructural('hstack_group')}>+ HStack Group</button>
                {selectedCount > 0 && (
                  <button type="button" className={`${styles.btn} ${styles.btnDanger}`} style={{ fontSize: '0.78rem' }}
                    onClick={deleteSelected}>Delete {selectedCount} selected</button>
                )}
              </div>
            </div>

            <div style={{ fontSize: '0.73rem', color: '#94a3b8', marginBottom: '0.4rem' }}>
              Drag to reorder · Click ✎ to edit inline · Click + to add children · Multiselect then Delete
            </div>

            {totalCount === 0 && (
              <div className={styles.emptyState}>No fields yet. Use the buttons above to add fields or a tab container.</div>
            )}

            {totalCount > 0 && (
              <div style={{ border: '1px solid #e2e8f0', borderRadius: '6px', overflow: 'hidden' }}>
                <Tree
                  value={nodes}
                  expandedKeys={expandedKeys}
                  onToggle={e => setExpandedKeys(e.value as Record<string, boolean>)}
                  selectionMode="multiple"
                  selectionKeys={selectedKeys}
                  onSelectionChange={e => setSelectedKeys(e.value as Record<string, boolean>)}
                  dragdropScope="field-config"
                  onDragDrop={handleDragDrop}
                  nodeTemplate={nodeTemplate}
                  style={{ fontSize: '0.85rem' }}
                  pt={{
                    node: { style: { borderLeft: '3px solid transparent' } },
                  }}
                />
              </div>
            )}
          </div>

          {errors.length > 0 && (
            <div className={styles.error} style={{ marginTop: '0.75rem' }}>
              {errors.map((msg, i) => <div key={i}>{msg}</div>)}
            </div>
          )}
          {success && <div className={styles.success} style={{ marginTop: '0.5rem' }}>{success}</div>}

          <div className={styles.row} style={{ marginTop: '1rem' }}>
            <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
              onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save Draft'}
            </button>
            <button type="button" className={`${styles.btn} ${styles.btnSuccess}`}
              onClick={handlePublish} disabled={publishing}>
              {publishing ? 'Publishing…' : 'Publish Draft'}
            </button>
          </div>
        </div>
      ) : (
        <div className={styles.emptyState}>No draft version exists.</div>
      )}
    </div>
  )
}


// ── Config Detail ─────────────────────────────────────────────────────────────

interface ConfigDetailProps {
  configId: string
  onBack: () => void
}

function ConfigDetail({ configId, onBack }: ConfigDetailProps) {
  const [config, setConfig] = useState<FieldConfigOut | null>(null)
  const [published, setPublished] = useState<ConfigVersionOut | null>(null)
  const [allTypes, setAllTypes] = useState<UDMTypeOut[]>([])
  const [allConfigs, setAllConfigs] = useState<FieldConfigOut[]>([])
  const [editName, setEditName] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [editingMeta, setEditingMeta] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [assignTypeId, setAssignTypeId] = useState('')
  const [assigningType, setAssigningType] = useState(false)

  const load = useCallback(async () => {
    try {
      const cfg = await fetch(`/api/udm/configs/${configId}/`, { credentials: 'include' })
        .then(r => r.json() as Promise<FieldConfigOut>)
      setConfig(cfg)
      setEditName(cfg.name)
      setEditDesc(cfg.description)
    } catch { /* ignore */ }

    try {
      const pub = await udmGetPublishedVersion(configId)
      setPublished(pub)
    } catch { setPublished(null) }

    try {
      const types = await udmListTypes()
      setAllTypes(types)
    } catch { /* ignore */ }

    try {
      const cfgs = await udmListConfigs()
      setAllConfigs(cfgs)
    } catch { /* ignore */ }
  }, [configId])

  useEffect(() => { void load() }, [load])

  async function handleSaveMeta() {
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const c = await udmUpdateConfig(configId, { name: editName, description: editDesc })
      setConfig(c)
      setEditingMeta(false)
      setSuccess('Config updated.')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed')
    } finally {
      setSaving(false)
    }
  }

  async function handleAssignType() {
    if (!assignTypeId) return
    setAssigningType(true)
    setError(null)
    try {
      const typeName = allTypes.find(t => t.id === assignTypeId)?.name ?? assignTypeId
      await udmUpdateType(assignTypeId, configId)
      setSuccess(`Config assigned to type "${typeName}"`)
      setAssignTypeId('')
      void load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Assign failed')
    } finally {
      setAssigningType(false)
    }
  }

  if (!config) return <div className={styles.emptyState}>Loading…</div>

  const languages = config.languages.map(l => l.code)

  return (
    <div>
      <div className={styles.detailHeader}>
        <button type="button" className={styles.backBtn} onClick={onBack}>← Back to Configs</button>
        <h2 className={styles.pageTitle} style={{ margin: 0 }}>{config.name}</h2>
      </div>

      {/* Meta */}
      <div className={styles.section}>
        <div className={styles.sectionTitle}>Config Info</div>
        {editingMeta ? (
          <>
            <div className={styles.row}>
              <div className={styles.formGroup}>
                <label className={styles.label}>Name *</label>
                <input className={styles.input} value={editName} onChange={e => setEditName(e.target.value)} />
              </div>
              <div className={styles.formGroup} style={{ flex: 2 }}>
                <label className={styles.label}>Description</label>
                <input className={styles.input} value={editDesc} onChange={e => setEditDesc(e.target.value)} />
              </div>
            </div>
            {error && <div className={styles.error}>{error}</div>}
            {success && <div className={styles.success}>{success}</div>}
            <div className={styles.row} style={{ marginTop: '0.75rem' }}>
              <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
                onClick={handleSaveMeta} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                onClick={() => setEditingMeta(false)}>Cancel</button>
            </div>
          </>
        ) : (
          <div className={styles.row}>
            <div>
              <div><strong>Name:</strong> {config.name}</div>
              {config.description && <div><strong>Description:</strong> {config.description}</div>}
              <div style={{ marginTop: '0.5rem' }}>
                <strong>Languages:</strong>{' '}
                <span className={styles.langGrid}>
                  {config.languages.map(l => (
                    <span key={l.code} className={`${styles.langTag} ${l.is_default ? styles.langTagDefault : ''}`}>
                      {l.code}{l.is_default ? ' (default)' : ''}
                    </span>
                  ))}
                </span>
              </div>
              <div style={{ marginTop: '0.5rem' }}>
                <strong>Stale entities:</strong> {config.stale_entity_count}
              </div>
              {config.type_ids.length > 0 && (
                <div style={{ marginTop: '0.5rem' }}>
                  <strong>Used by types:</strong> {config.type_ids.map(id => (
                    <span key={id} className={styles.monoText} style={{ marginLeft: '0.5rem', fontSize: '0.8rem' }}>{id}</span>
                  ))}
                </div>
              )}
            </div>
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={() => setEditingMeta(true)} style={{ alignSelf: 'flex-start' }}>
              Edit
            </button>
          </div>
        )}

        <div className={styles.subsection}>
          <div className={styles.subsectionTitle}>Assign to UDM Type</div>
          {allTypes.length === 0 ? (
            <div className={styles.info}>No UDM types available. Create types in the Types tab first.</div>
          ) : (
            <div className={styles.row}>
              <div className={styles.formGroup}>
                <label className={styles.label}>Type</label>
                <select className={styles.select} value={assignTypeId} onChange={e => setAssignTypeId(e.target.value)}>
                  <option value="">— select type —</option>
                  {allTypes
                    .filter(t => !config?.type_ids.includes(t.id))
                    .map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </div>
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                onClick={handleAssignType} disabled={assigningType || !assignTypeId}
                style={{ alignSelf: 'flex-end' }}>
                {assigningType ? 'Assigning…' : 'Assign'}
              </button>
            </div>
          )}
          {error && !editingMeta && <div className={styles.error}>{error}</div>}
          {success && !editingMeta && <div className={styles.success}>{success}</div>}
        </div>
      </div>

      {/* Published version summary */}
      {published && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>
            Published Version {statusBadge('published')}
            {published.published_at && (
              <span className={styles.info} style={{ marginLeft: '0.5rem', fontSize: '0.85rem' }}>
                — {new Date(published.published_at).toLocaleString()}
              </span>
            )}
          </div>
          <div><strong>Fields:</strong> {published.fields.length}</div>
          <div style={{ marginTop: '0.5rem' }}>
            {published.fields.map(f => (
              <span key={f.slug} className={styles.ruleTag}>{f.slug} ({f.data_type})</span>
            ))}
          </div>
        </div>
      )}

      {/* Draft editor */}
      <div className={styles.section}>
        <DraftEditor
          configId={configId}
          languages={languages}
          allConfigs={allConfigs}
          onSaved={() => void load()}
        />
      </div>
    </div>
  )
}

// ── Configs Tab ───────────────────────────────────────────────────────────────

function ConfigsTab() {
  const [configs, setConfigs] = useState<FieldConfigOut[]>([])
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<ConfigView>('list')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newLangs, setNewLangs] = useState('en')
  const [createError, setCreateError] = useState<string | null>(null)

  const loadConfigs = useCallback(async () => {
    setLoading(true)
    try {
      const list = await udmListConfigs()
      setConfigs(list)
    } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => { void loadConfigs() }, [loadConfigs])

  async function handleCreate() {
    setCreateError(null)
    const codes = newLangs.split(',').map(s => s.trim()).filter(Boolean)
    if (!newName.trim() || codes.length === 0) {
      setCreateError('Name and at least one language code are required.')
      return
    }
    try {
      const languages = codes.map((code, i) => ({
        code, label: code.toUpperCase(), is_default: i === 0, sort_order: i,
      }))
      await udmCreateConfig({ name: newName, description: newDesc, languages })
      setNewName('')
      setNewDesc('')
      setNewLangs('en')
      setCreating(false)
      void loadConfigs()
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Create failed')
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`Delete config "${name}"? This cannot be undone.`)) return
    try {
      await udmDeleteConfig(id)
      void loadConfigs()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  if (view === 'detail' && selectedId) {
    return (
      <ConfigDetail
        configId={selectedId}
        onBack={() => { setView('list'); void loadConfigs() }}
      />
    )
  }

  return (
    <div>
      <div className={styles.row} style={{ marginBottom: '1rem', justifyContent: 'flex-end' }}>
        <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
          onClick={() => setCreating(!creating)}>
          {creating ? 'Cancel' : '+ New Field Config'}
        </button>
      </div>

      {creating && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Create Field Config</div>
          <div className={styles.row}>
            <div className={styles.formGroup}>
              <label className={styles.label}>Name *</label>
              <input className={styles.input} value={newName} onChange={e => setNewName(e.target.value)}
                placeholder="Standard Workshop Form" />
            </div>
            <div className={styles.formGroup} style={{ flex: 2 }}>
              <label className={styles.label}>Description</label>
              <input className={styles.input} value={newDesc} onChange={e => setNewDesc(e.target.value)} />
            </div>
            <div className={styles.formGroup}>
              <label className={styles.label}>Language codes (comma-separated) *</label>
              <input className={styles.input} value={newLangs} onChange={e => setNewLangs(e.target.value)}
                placeholder="en, de" />
            </div>
          </div>
          {createError && <div className={styles.error}>{createError}</div>}
          <div className={styles.row} style={{ marginTop: '0.75rem' }}>
            <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={handleCreate}>
              Create
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className={styles.emptyState}>Loading…</div>
      ) : configs.length === 0 ? (
        <div className={styles.emptyState}>No field configs yet.</div>
      ) : (
        <div className={styles.section}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Languages</th>
                <th>Stale Entities</th>
                <th>Used by Types</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {configs.map(cfg => (
                <tr key={cfg.id}>
                  <td><strong>{cfg.name}</strong></td>
                  <td>
                    <span className={styles.langGrid}>
                      {cfg.languages.map(l => (
                        <span key={l.code} className={`${styles.langTag} ${l.is_default ? styles.langTagDefault : ''}`}>
                          {l.code}
                        </span>
                      ))}
                    </span>
                  </td>
                  <td>{cfg.stale_entity_count}</td>
                  <td>{cfg.type_ids.length}</td>
                  <td>
                    <div className={styles.tableActions}>
                      <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                        onClick={() => { setSelectedId(cfg.id); setView('detail') }}>
                        Open
                      </button>
                      <button type="button" className={`${styles.btn} ${styles.btnDanger}`}
                        onClick={() => void handleDelete(cfg.id, cfg.name)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Policies Tab ──────────────────────────────────────────────────────────────

function PoliciesTab() {
  const [policies, setPolicies] = useState<PolicyOut[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [editSlug, setEditSlug] = useState<string | null>(null)
  const [newSlug, setNewSlug] = useState('')
  const [source, setSource] = useState('')
  const [editSource, setEditSource] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const loadPolicies = useCallback(async () => {
    setLoading(true)
    try {
      const list = await udmListPolicies()
      setPolicies(list)
    } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => { void loadPolicies() }, [loadPolicies])

  async function handleCreate() {
    setError(null)
    if (!newSlug.trim() || !source.trim()) {
      setError('Slug and source are required.')
      return
    }
    try {
      await udmCreatePolicy({ slug: newSlug.trim(), source })
      setNewSlug('')
      setSource('')
      setCreating(false)
      setSuccess('Policy created.')
      void loadPolicies()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed')
    }
  }

  async function handleUpdate(slug: string) {
    setError(null)
    try {
      await udmUpdatePolicy(slug, { source: editSource })
      setEditSlug(null)
      setSuccess('Policy updated.')
      void loadPolicies()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed')
    }
  }

  async function handleDelete(slug: string) {
    if (!confirm(`Delete policy "${slug}"?`)) return
    try {
      await udmDeletePolicy(slug)
      void loadPolicies()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  return (
    <div>
      <div className={styles.row} style={{ marginBottom: '1rem', justifyContent: 'flex-end' }}>
        <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
          onClick={() => setCreating(!creating)}>
          {creating ? 'Cancel' : '+ New Policy'}
        </button>
      </div>

      {creating && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Create Rego Policy</div>
          <div className={styles.formGroup}>
            <label className={styles.label}>Slug *</label>
            <input className={styles.input} value={newSlug} onChange={e => setNewSlug(e.target.value)}
              placeholder="staff_full_access" />
          </div>
          <div className={styles.formGroup} style={{ marginTop: '0.5rem' }}>
            <label className={styles.label}>Rego Source *</label>
            <textarea className={styles.textarea} rows={8} value={source}
              onChange={e => setSource(e.target.value)}
              placeholder={'package udm\n\ndefault allow := false\n\nallow {\n  input.user.is_staff\n}'} />
          </div>
          {error && <div className={styles.error}>{error}</div>}
          {success && <div className={styles.success}>{success}</div>}
          <div className={styles.row} style={{ marginTop: '0.75rem' }}>
            <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={handleCreate}>
              Create
            </button>
          </div>
        </div>
      )}

      {!loading && success && !creating && <div className={styles.success} style={{ marginBottom: '0.5rem' }}>{success}</div>}

      {loading ? (
        <div className={styles.emptyState}>Loading…</div>
      ) : policies.length === 0 ? (
        <div className={styles.emptyState}>No policies yet.</div>
      ) : (
        policies.map(policy => (
          <div key={policy.slug} className={styles.section}>
            <div className={styles.fieldCardHeader}>
              <span className={styles.monoText} style={{ fontWeight: 600 }}>{policy.slug}</span>
              <div className={styles.tableActions}>
                {editSlug === policy.slug ? (
                  <>
                    <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
                      onClick={() => void handleUpdate(policy.slug)}>Save</button>
                    <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                      onClick={() => setEditSlug(null)}>Cancel</button>
                  </>
                ) : (
                  <>
                    <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                      onClick={() => { setEditSlug(policy.slug); setEditSource(policy.source) }}>Edit</button>
                    <button type="button" className={`${styles.btn} ${styles.btnDanger}`}
                      onClick={() => void handleDelete(policy.slug)}>Delete</button>
                  </>
                )}
              </div>
            </div>
            {editSlug === policy.slug ? (
              <>
                <textarea className={styles.textarea} rows={10} value={editSource}
                  onChange={e => setEditSource(e.target.value)} style={{ width: '100%' }} />
                {error && <div className={styles.error}>{error}</div>}
              </>
            ) : (
              <pre className={styles.monoText} style={{ margin: 0, overflow: 'auto', maxHeight: '200px', padding: '0.5rem', background: '#f8f8f8', borderRadius: '4px', fontSize: '0.8rem' }}>
                {policy.source}
              </pre>
            )}
          </div>
        ))
      )}
    </div>
  )
}

// ── Policy Evaluator ──────────────────────────────────────────────────────────

const ACTIONS = ['view', 'save', 'transition', 'delete']

interface PolicyEvaluatorProps {
  typeId: string
}

function PolicyEvaluator({ typeId }: PolicyEvaluatorProps) {
  const [entities, setEntities] = useState<EntityAutocompleteItem[]>([])
  const [users, setUsers] = useState<UserAutocompleteItem[]>([])
  const [entityId, setEntityId] = useState('')
  const [userId, setUserId] = useState('')
  const [action, setAction] = useState('view')
  const [transitionName, setTransitionName] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<PolicyEvalOut | null>(null)
  const [evalError, setEvalError] = useState<string | null>(null)
  const [activeResultTab, setActiveResultTab] = useState<'output' | 'input' | 'policies' | 'prints'>('output')

  useEffect(() => {
    udmSearchEntities('', typeId).then(setEntities).catch(() => {})
    udmSearchUsers('').then(setUsers).catch(() => {})
  }, [typeId])

  async function handleRun() {
    if (!entityId || !userId) return
    setRunning(true)
    setResult(null)
    setEvalError(null)
    try {
      const out = await udmEvalPolicy(
        typeId, entityId, userId, action,
        action === 'transition' && transitionName ? transitionName : undefined,
      )
      setResult(out)
    } catch (e) {
      setEvalError(e instanceof Error ? e.message : 'Evaluation failed')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div>
      {/* Controls */}
      <div className={styles.row} style={{ flexWrap: 'wrap', gap: '0.75rem', marginBottom: '0.75rem' }}>
        <div className={styles.formGroup} style={{ minWidth: '200px' }}>
          <label className={styles.label}>Entity</label>
          <select className={styles.select} value={entityId} onChange={e => setEntityId(e.target.value)}>
            <option value="">— select entity —</option>
            {entities.map(e => (
              <option key={e.id} value={e.id}>
                {e.display && e.display !== e.id ? `${e.display} (${e.id.slice(0, 8)}…)` : e.id}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.formGroup} style={{ minWidth: '180px' }}>
          <label className={styles.label}>User</label>
          <select className={styles.select} value={userId} onChange={e => setUserId(e.target.value)}>
            <option value="">— select user —</option>
            {users.map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}
          </select>
        </div>

        <div className={styles.formGroup} style={{ minWidth: '130px', flex: 'none' }}>
          <label className={styles.label}>Action</label>
          <select className={styles.select} value={action} onChange={e => setAction(e.target.value)}>
            {ACTIONS.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>

        {action === 'transition' && (
          <div className={styles.formGroup} style={{ minWidth: '140px' }}>
            <label className={styles.label}>Transition name</label>
            <input className={styles.input} value={transitionName}
              onChange={e => setTransitionName(e.target.value)} placeholder="submit" />
          </div>
        )}

        <button
          type="button"
          className={`${styles.btn} ${styles.btnPrimary}`}
          style={{ alignSelf: 'flex-end' }}
          onClick={handleRun}
          disabled={running || !entityId || !userId}
        >
          {running ? 'Evaluating…' : 'Evaluate'}
        </button>
      </div>

      {evalError && <div className={styles.error}>{evalError}</div>}

      {result && (
        <div>
          {/* Quick verdict */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.75rem',
            marginBottom: '0.75rem', padding: '0.6rem 0.9rem',
            background: result.error ? '#fff3cd' : result.output.allow ? '#d4edda' : '#f8d7da',
            borderRadius: '6px',
            border: `1px solid ${result.error ? '#ffc107' : result.output.allow ? '#c3e6cb' : '#f5c6cb'}`,
          }}>
            <span style={{ fontWeight: 700, fontSize: '1rem' }}>
              {result.error ? '⚠ Error' : result.output.allow ? '✓ Allow' : '✗ Deny'}
            </span>
            {result.error && (
              <span style={{ fontSize: '0.875rem', color: '#856404' }}>{result.error}</span>
            )}
            {!result.error && (
              <span style={{ fontSize: '0.875rem', color: '#555' }}>
                {(result.output.messages as unknown[]).length} message{(result.output.messages as unknown[]).length !== 1 ? 's' : ''}
                {' · '}
                {(result.output.viewable_fields as string[]).length} viewable fields
                {' · '}
                {(result.output.editable_fields as string[]).length} editable fields
              </span>
            )}
          </div>

          {/* Messages */}
          {(result.output.messages as unknown[]).length > 0 && (
            <div style={{ marginBottom: '0.75rem' }}>
              {(result.output.messages as Array<string | Record<string, unknown>>).map((m, i) => {
                const level = typeof m === 'object' && m !== null ? String(m['level'] ?? '') : ''
                const text = typeof m === 'string' ? m : JSON.stringify(m)
                const color = level === 'critical' || level === 'error' ? '#dc2626'
                  : level === 'warning' ? '#d97706' : '#0066cc'
                return (
                  <div key={i} style={{ fontSize: '0.85rem', color, marginBottom: '0.2rem' }}>
                    {level && <strong>[{level}] </strong>}{text}
                  </div>
                )
              })}
            </div>
          )}

          {/* Field lists */}
          {((result.output.viewable_fields as string[]).length > 0 || (result.output.editable_fields as string[]).length > 0) && (
            <div style={{ display: 'flex', gap: '1.5rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
              {(result.output.viewable_fields as string[]).length > 0 && (
                <div>
                  <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#555', marginBottom: '0.25rem' }}>Viewable fields</div>
                  <div className={styles.langGrid}>
                    {(result.output.viewable_fields as string[]).map(f => (
                      <span key={f} className={styles.ruleTag}>{f}</span>
                    ))}
                  </div>
                </div>
              )}
              {(result.output.editable_fields as string[]).length > 0 && (
                <div>
                  <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#555', marginBottom: '0.25rem' }}>Editable fields</div>
                  <div className={styles.langGrid}>
                    {(result.output.editable_fields as string[]).map(f => (
                      <span key={f} className={styles.ruleTag} style={{ background: '#d4edda', color: '#155724' }}>{f}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Detail tabs */}
          <div className={styles.tabs} style={{ marginBottom: '0.5rem' }}>
            {(['output', 'input', 'policies', 'prints'] as const).map(tab => {
              if (tab === 'prints' && !(result.prints?.length)) return null
              return (
                <button key={tab} type="button"
                  className={`${styles.tab} ${activeResultTab === tab ? styles.tabActive : ''}`}
                  onClick={() => setActiveResultTab(tab)}>
                  {tab === 'output' ? 'Full Output'
                    : tab === 'input' ? 'Input Document'
                    : tab === 'policies' ? `Policies (${result.policies.length})`
                    : `Prints (${result.prints!.length})`}
                </button>
              )
            })}
          </div>

          {activeResultTab === 'output' && (
            <pre className={styles.monoText} style={{ margin: 0, padding: '0.75rem', background: '#f8f8f8', borderRadius: '6px', overflow: 'auto', maxHeight: '400px', fontSize: '0.8rem', border: '1px solid #e0e0e0' }}>
              {JSON.stringify(result.output, null, 2)}
            </pre>
          )}

          {activeResultTab === 'input' && (
            <pre className={styles.monoText} style={{ margin: 0, padding: '0.75rem', background: '#f8f8f8', borderRadius: '6px', overflow: 'auto', maxHeight: '400px', fontSize: '0.8rem', border: '1px solid #e0e0e0' }}>
              {JSON.stringify(result.input_document, null, 2)}
            </pre>
          )}

          {activeResultTab === 'policies' && (
            <div>
              {result.policies.length === 0 ? (
                <div className={styles.emptyState}>No policies assigned to this type.</div>
              ) : result.policies.map((p: Record<string, string>) => {
                const slug = p['slug'] as string
                const coverageFile = result.coverage?.find(f => f.path === `policy_${slug}.rego`)
                const coveredSet = new Set((coverageFile?.covered ?? []) as number[])
                const notCoveredSet = new Set((coverageFile?.not_covered ?? []) as number[])

                // Map line number → print values emitted at that line for this policy
                const printsByLine = new Map<number, string[]>()
                for (const raw of result.prints ?? []) {
                  const m = raw.match(/^policy_(.+?)\.rego:(\d+): (.*)$/)
                  if (m && m[1] === slug) {
                    const lineNo = parseInt(m[2], 10)
                    const val = m[3]
                    if (!printsByLine.has(lineNo)) printsByLine.set(lineNo, [])
                    printsByLine.get(lineNo)!.push(val)
                  }
                }

                const lines = p['source'].split('\n')
                return (
                  <div key={slug} style={{ marginBottom: '0.75rem' }}>
                    <div className={styles.monoText} style={{ fontWeight: 600, marginBottom: '0.25rem' }}>
                      {slug}
                      {coverageFile && (
                        <span style={{ fontWeight: 400, fontSize: '0.75rem', color: '#555', marginLeft: '0.6rem' }}>
                          {(coverageFile.covered as number[]).length} covered · {(coverageFile.not_covered as number[]).length} not covered
                        </span>
                      )}
                    </div>
                    <div className={styles.monoText} style={{ padding: '0.5rem 0', background: '#f8f8f8', borderRadius: '6px', overflow: 'auto', maxHeight: '500px', fontSize: '0.8rem', border: '1px solid #e0e0e0' }}>
                      {lines.map((line, idx) => {
                        const lineNo = idx + 1
                        const bg = coveredSet.has(lineNo) ? '#c6f6d5'
                          : notCoveredSet.has(lineNo) ? '#fed7d7'
                          : 'transparent'
                        const linePrints = printsByLine.get(lineNo)
                        return (
                          <div key={idx}>
                            <div style={{ display: 'flex', background: bg }}>
                              <span style={{ minWidth: '2.8rem', padding: '0 0.5rem', color: '#999', userSelect: 'none', textAlign: 'right', flexShrink: 0 }}>
                                {lineNo}
                              </span>
                              <span style={{ padding: '0 0.5rem', whiteSpace: 'pre' }}>{line}</span>
                            </div>
                            {linePrints?.map((val, pi) => (
                              <div key={pi} style={{ display: 'flex', background: '#1e1e1e' }}>
                                <span style={{ minWidth: '2.8rem', padding: '0 0.5rem', color: '#555', userSelect: 'none', textAlign: 'right', flexShrink: 0 }}>▶</span>
                                <span style={{ padding: '0 0.5rem', whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: '#d4d4d4' }}>{val}</span>
                              </div>
                            ))}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {activeResultTab === 'prints' && result.prints && result.prints.length > 0 && (
            <div style={{ background: '#1e1e1e', borderRadius: '6px', overflow: 'auto', maxHeight: '400px', padding: '0.75rem', border: '1px solid #333' }}>
              {result.prints.map((line, i) => (
                <div key={i} className={styles.monoText} style={{ fontSize: '0.8rem', color: '#d4d4d4', marginBottom: '0.15rem', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {line}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Types Tab ─────────────────────────────────────────────────────────────────

interface TypeDetailProps {
  udmType: UDMTypeOut
  onBack: () => void
  allConfigs: FieldConfigOut[]
  allPolicies: PolicyOut[]
  onUpdated: (t: UDMTypeOut) => void
  isSuperuser: boolean
}

function TypeDetail({ udmType, onBack, allConfigs, allPolicies, onUpdated, isSuperuser }: TypeDetailProps) {
  const [policies, setPolicies] = useState<PolicyOut[]>([])
  const [assignSlug, setAssignSlug] = useState('')
  const [configId, setConfigId] = useState(udmType.field_config_id ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const loadPolicies = useCallback(async () => {
    try {
      const list = await udmListTypePolicies(udmType.id)
      setPolicies(list)
    } catch { /* ignore */ }
  }, [udmType.id])

  useEffect(() => { void loadPolicies() }, [loadPolicies])

  async function handleAssignConfig() {
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const updated = await udmUpdateType(udmType.id, configId || null)
      onUpdated(updated)
      setSuccess('Config assigned.')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Assign failed')
    } finally {
      setSaving(false)
    }
  }

  async function handleAssignPolicy() {
    if (!assignSlug) return
    setSaving(true)
    setError(null)
    try {
      await udmAssignPolicy(udmType.id, { policy_slug: assignSlug, sort_order: policies.length })
      setAssignSlug('')
      setSuccess('Policy assigned.')
      void loadPolicies()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Assign failed')
    } finally {
      setSaving(false)
    }
  }

  async function handleRemovePolicy(slug: string) {
    try {
      await udmRemovePolicy(udmType.id, slug)
      void loadPolicies()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Remove failed')
    }
  }

  return (
    <div>
      <div className={styles.detailHeader}>
        <button type="button" className={styles.backBtn} onClick={onBack}>← Back to Types</button>
        <h2 className={styles.pageTitle} style={{ margin: 0 }}>{udmType.name}</h2>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Field Config Assignment</div>
        <div className={styles.row}>
          <div className={styles.formGroup}>
            <label className={styles.label}>Current Config</label>
            <div style={{ fontSize: '0.875rem', color: '#555', padding: '0.45rem 0' }}>
              {udmType.field_config_id
                ? (allConfigs.find(c => c.id === udmType.field_config_id)?.name ?? udmType.field_config_id)
                : 'None assigned'}
            </div>
          </div>
          <div className={styles.formGroup}>
            <label className={styles.label}>Assign Config</label>
            <select className={styles.select} value={configId} onChange={e => setConfigId(e.target.value)}>
              <option value="">— no config —</option>
              {allConfigs.map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
          <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
            onClick={handleAssignConfig} disabled={saving} style={{ alignSelf: 'flex-end' }}>
            {saving ? 'Saving…' : 'Apply'}
          </button>
        </div>
        {error && <div className={styles.error}>{error}</div>}
        {success && <div className={styles.success}>{success}</div>}
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Assigned Policies</div>
        {policies.length === 0 ? (
          <div className={styles.emptyState}>No policies assigned.</div>
        ) : (
          <table className={styles.table}>
            <thead><tr><th>Slug</th><th>Actions</th></tr></thead>
            <tbody>
              {policies.map(p => (
                <tr key={p.slug}>
                  <td><span className={styles.monoText}>{p.slug}</span></td>
                  <td>
                    <button type="button" className={`${styles.btn} ${styles.btnDanger}`}
                      onClick={() => void handleRemovePolicy(p.slug)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className={styles.subsection}>
          <div className={styles.subsectionTitle}>Add Policy</div>
          <div className={styles.row}>
            <div className={styles.formGroup}>
              <select className={styles.select} value={assignSlug} onChange={e => setAssignSlug(e.target.value)}>
                <option value="">— select policy —</option>
                {allPolicies
                  .filter(p => !policies.some(ap => ap.slug === p.slug))
                  .map(p => <option key={p.slug} value={p.slug}>{p.slug}</option>)}
              </select>
            </div>
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={handleAssignPolicy} disabled={!assignSlug}>
              Assign
            </button>
          </div>
        </div>
      </div>

      {isSuperuser && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Policy Evaluator</div>
          <PolicyEvaluator typeId={udmType.id} />
        </div>
      )}
    </div>
  )
}

function TypesTab() {
  const { permissions } = usePermissions()
  const isSuperuser = !!permissions?.is_superuser
  const [types, setTypes] = useState<UDMTypeOut[]>([])
  const [configs, setConfigs] = useState<FieldConfigOut[]>([])
  const [allPolicies, setAllPolicies] = useState<PolicyOut[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedType, setSelectedType] = useState<UDMTypeOut | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [t, c, p] = await Promise.all([udmListTypes(), udmListConfigs(), udmListPolicies()])
      setTypes(t)
      setConfigs(c)
      setAllPolicies(p)
    } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => { void loadAll() }, [loadAll])

  async function handleCreate() {
    setCreateError(null)
    if (!newName.trim()) { setCreateError('Name is required'); return }
    try {
      const t = await udmCreateType({ name: newName.trim(), description: newDesc })
      setTypes(prev => [...prev, t])
      setNewName('')
      setNewDesc('')
      setCreating(false)
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Create failed')
    }
  }

  if (selectedType) {
    return (
      <TypeDetail
        udmType={selectedType}
        onBack={() => { setSelectedType(null); void loadAll() }}
        allConfigs={configs}
        allPolicies={allPolicies}
        onUpdated={updated => setSelectedType(updated)}
        isSuperuser={isSuperuser}
      />
    )
  }

  return (
    <div>
      <div className={styles.row} style={{ marginBottom: '1rem', justifyContent: 'flex-end' }}>
        <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
          onClick={() => setCreating(!creating)}>
          {creating ? 'Cancel' : '+ New UDM Type'}
        </button>
      </div>

      {creating && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Create UDM Type</div>
          <div className={styles.row}>
            <div className={styles.formGroup}>
              <label className={styles.label}>Name *</label>
              <input className={styles.input} value={newName} onChange={e => setNewName(e.target.value)}
                placeholder="Workshop" />
            </div>
            <div className={styles.formGroup} style={{ flex: 2 }}>
              <label className={styles.label}>Description</label>
              <input className={styles.input} value={newDesc} onChange={e => setNewDesc(e.target.value)} />
            </div>
          </div>
          {createError && <div className={styles.error}>{createError}</div>}
          <div className={styles.row} style={{ marginTop: '0.75rem' }}>
            <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={handleCreate}>
              Create
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className={styles.emptyState}>Loading…</div>
      ) : types.length === 0 ? (
        <div className={styles.emptyState}>No UDM types yet. Create one above.</div>
      ) : (
        <div className={styles.section}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Field Config</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {types.map(t => {
                const cfg = t.field_config_id ? configs.find(c => c.id === t.field_config_id) : null
                return (
                  <tr key={t.id}>
                    <td><strong>{t.name}</strong></td>
                    <td style={{ color: '#666', fontSize: '0.875rem' }}>{t.description || '—'}</td>
                    <td>{cfg ? cfg.name : <span style={{ color: '#999' }}>Not assigned</span>}</td>
                    <td>
                      <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                        onClick={() => setSelectedType(t)}>
                        Manage
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function UdmAdminPage() {
  useTranslation()
  const [tab, setTab] = useState<AdminTab>('configs')

  return (
    <div className={styles.page}>
      <h1 className={styles.pageTitle}>UDM Configuration Admin</h1>

      <div className={styles.tabs}>
        {([
          { key: 'configs', label: 'Field Configs' },
          { key: 'types', label: 'UDM Types' },
          { key: 'policies', label: 'Rego Policies' },
          { key: 'migrations', label: 'Bulk Migration' },
        ] as const).map(({ key, label }) => (
          <button key={key} type="button"
            className={`${styles.tab} ${tab === key ? styles.tabActive : ''}`}
            onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'configs' && <ConfigsTab />}
      {tab === 'types' && <TypesTab />}
      {tab === 'policies' && <PoliciesTab />}
      {tab === 'migrations' && <BulkMigrationTab />}
    </div>
  )
}
