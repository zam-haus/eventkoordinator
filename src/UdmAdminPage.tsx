import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { UdfMarkdown } from './UdfMarkdown'
import { useTranslation } from 'react-i18next'
import { Tree } from 'primereact/tree'
import type { TreeNode } from 'primereact/treenode'
import { DataTable } from 'primereact/datatable'
import { Column } from 'primereact/column'
import { MultiSelect, type MultiSelectChangeEvent } from 'primereact/multiselect'
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
  udmUpdateTypeMeta,
  udmGetTypeDescriptions,
  udmDeleteType,
  udmListTypePolicies,
  udmAssignPolicy,
  udmRemovePolicy,
  udmListTypes,
  udmCreateType,
  udmSearchEntities,
  udmSearchUsers,
  udmEvalPolicy,
  udmEvalPolicyNodes,
  udmListWorkflows,
  type FieldConfigOut,
  type ConfigVersionOut,
  type FieldDefinitionIn,
  type FieldDefinitionOut,
  type FormElementIn,
  type FormElementOut,
  type PolicyOut,
  type UDMTypeOut,
  type DataType,
  type PolicyEvalOut,
  type EvalNodeOut,
  type EntityAutocompleteItem,
  type UserAutocompleteItem,
  type WorkflowDefinitionOut,
} from './apiUdm'
import { usePermissions } from './usePermissions'
import { BulkMigrationTab } from './UdmMigration'
import { BundleTab } from './UdmBundleTab'
import { WorkflowEditor } from './WorkflowEditor'
import styles from './UdmAdminPage.module.css'

type AdminTab = 'configs' | 'policies' | 'types' | 'migrations' | 'bundle' | 'workflow'

// ── Policy evaluator: field-grant tree ────────────────────────────────────────
// Renders the entity tree from the evaluator's input document and marks every
// field with its grant state (✏ editable, 👁 view only, struck-through hidden).

interface GrantTreeNode {
  id: string
  schema_id?: string
  parent_field_slug?: string | null
  fields: Record<string, unknown>
  children: Record<string, GrantTreeNode[]>
}

function FieldGrantTree({ node, schemas, viewable, editable, label }: {
  node: GrantTreeNode
  schemas: Record<string, { slug?: string }>
  viewable: Record<string, string[]>
  editable: Record<string, string[]>
  label: string
}) {
  const canView = new Set(viewable[node.id] ?? [])
  const canEdit = new Set(editable[node.id] ?? [])
  const schemaSlug = node.schema_id ? schemas[node.schema_id]?.slug : undefined
  const fieldSlugs = Object.keys(node.fields ?? {}).sort()
  const childEntries = Object.entries(node.children ?? {})
  return (
    <div style={{ fontSize: '0.85rem', fontFamily: 'monospace' }}>
      <div style={{ fontWeight: 600, color: '#333' }}>
        {label}
        {schemaSlug && <span style={{ color: '#888', fontWeight: 400 }}> ({schemaSlug})</span>}
        <span style={{ color: '#bbb', fontWeight: 400 }}> {node.id.slice(0, 8)}…</span>
      </div>
      <ul style={{ listStyle: 'none', margin: '0.15rem 0 0.3rem 0', paddingLeft: '1.2rem', borderLeft: '1px solid #e2e8f0' }}>
        {fieldSlugs.map(slug => {
          const isEdit = canEdit.has(slug)
          const isView = canView.has(slug)
          const color = isEdit ? '#155724' : isView ? '#0066cc' : '#999'
          return (
            <li key={slug} style={{ color, padding: '0.05rem 0' }}>
              <span style={{ display: 'inline-block', width: '1.4rem' }}>{isEdit ? '✏' : isView ? '👁' : '·'}</span>
              {isView || isEdit ? slug : <s>{slug}</s>}
            </li>
          )
        })}
        {childEntries.map(([slug, children]) => (
          <li key={`child-${slug}`} style={{ padding: '0.1rem 0' }}>
            {children.map((child, i) => (
              <FieldGrantTree key={child.id} node={child} schemas={schemas}
                viewable={viewable} editable={editable}
                label={`${slug}[${i}]`} />
            ))}
            {children.length === 0 && (
              <span style={{ color: '#bbb' }}>{slug}: (no items)</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}


const DATA_TYPES: DataType[] = [
  'text_short', 'text_long', 'text_markdown', 'text_richtext',
  'integer', 'float', 'boolean', 'date', 'time', 'datetime',
  'select_single', 'select_multi', 'image', 'file',
  'user_select', 'user_select_multi', 'group_select', 'group_select_multi',
  'submodel_select', 'submodel_list', 'entity_select', 'entity_select_multi',
  'slug_id', 'workflow',
]

const STRUCTURAL_TYPES: DataType[] = ['tab_container', 'tab', 'save_button', 'hstack', 'hstack_group', 'tab_prev', 'tab_next']

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

  // When a config is chosen, load its versions and default to the latest
  // published version (the up-to-date submodel schema), if no value is set.
  useEffect(() => {
    if (!selectedConfigId) { setVersions([]); return }
    setLoadingVersions(true)
    import('./apiUdm').then(({ udmListConfigVersions }) =>
      udmListConfigVersions(selectedConfigId)
        .then(vs => {
          setVersions(vs)
          // Default to the most recent published version when none is selected.
          if (!value) {
            const published = vs.filter(v => v.status === 'published')
              .sort((a, b) => (b.published_at ?? '').localeCompare(a.published_at ?? ''))
            if (published[0]) onChange(published[0].id)
          }
        })
        .catch(() => setVersions([]))
        .finally(() => setLoadingVersions(false))
    )
  }, [selectedConfigId]) // eslint-disable-line react-hooks/exhaustive-deps

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

  const publishedWorkflows = workflows.filter(wf => wf.published_version_id)

  return (
    <div className={styles.formGroup} style={{ gridColumn: '1 / -1' }}>
      <label className={styles.label}>Workflow Version * {loading && '(loading…)'}</label>
      <select
        className={styles.select}
        value={value ?? ''}
        onChange={e => onChange(e.target.value || null)}
      >
        <option value="">— select a published workflow —</option>
        {publishedWorkflows.map(wf => (
          <option key={wf.published_version_id} value={wf.published_version_id!}>
            {wf.name} ({wf.states.length} states, {wf.transitions.length} transitions)
          </option>
        ))}
      </select>
      {publishedWorkflows.length === 0 && !loading && (
        <div style={{ fontSize: '0.8rem', color: '#888', marginTop: '0.25rem' }}>
          No published workflows found. Create and publish one in the Workflow Editor first.
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
                    workflow_version_id: dt === 'workflow' ? (field.workflow_version_id ?? null) : null,
                  })
                }}>
                {DATA_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div className={styles.formGroup}>
              <label className={styles.label}>Flags</label>
              <label className={styles.checkbox}>
                <input type="checkbox" checked={field.is_localized}
                  onChange={e => setF({ is_localized: e.target.checked })} />
                Localized
              </label>
            </div>

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
                value={field.workflow_version_id ?? null}
                onChange={id => setF({ workflow_version_id: id })}
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
        <ConfigDraftEditor
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

type ConfigColKey = 'description' | 'languages' | 'entity_count' | 'stale_entity_count' | 'published_submodel_usage_count' | 'type_count' | 'version_count' | 'created_at' | 'last_published_at'
const CONFIG_COL_OPTIONS: { label: string; value: ConfigColKey }[] = [
  { label: 'Description', value: 'description' },
  { label: 'Languages', value: 'languages' },
  { label: 'Entities', value: 'entity_count' },
  { label: 'Stale Entities', value: 'stale_entity_count' },
  { label: 'Published Submodel Usages', value: 'published_submodel_usage_count' },
  { label: 'Used by Types', value: 'type_count' },
  { label: 'Versions', value: 'version_count' },
  { label: 'Created', value: 'created_at' },
  { label: 'Last Published', value: 'last_published_at' },
]
const CONFIG_COL_DEFAULT: ConfigColKey[] = [
  'languages', 'entity_count', 'stale_entity_count', 'published_submodel_usage_count',
  'type_count', 'version_count', 'created_at', 'last_published_at',
]

function isUnused(cfg: FieldConfigOut) {
  return cfg.entity_count === 0 && cfg.type_ids.length === 0 && cfg.published_submodel_usage_count === 0
}

function ConfigsTab({ selectedConfigId = null }: { selectedConfigId?: string | null }) {
  const navigate = useNavigate()
  const [configs, setConfigs] = useState<FieldConfigOut[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newLangs, setNewLangs] = useState('en')
  const [createError, setCreateError] = useState<string | null>(null)
  const [visibleCols, setVisibleCols] = useState<ConfigColKey[]>(CONFIG_COL_DEFAULT)
  const [hideUnused, setHideUnused] = useState(true)

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

  if (selectedConfigId) {
    return (
      <ConfigDetail
        configId={selectedConfigId}
        onBack={() => navigate('/udm-admin/configs')}
      />
    )
  }

  const vis = new Set(visibleCols)
  const displayed = hideUnused ? configs.filter(c => !isUnused(c)) : configs
  const hiddenCount = configs.length - displayed.length

  const tableHeader = (
    <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', justifyContent: 'space-between', flexWrap: 'wrap' }}>
      <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem', cursor: 'pointer' }}>
        <input type="checkbox" checked={hideUnused} onChange={e => setHideUnused(e.target.checked)} />
        Hide unused
        {hiddenCount > 0 && <span style={{ color: '#888' }}>({hiddenCount} hidden)</span>}
      </label>
      <MultiSelect
        value={visibleCols}
        options={CONFIG_COL_OPTIONS}
        onChange={(e: MultiSelectChangeEvent) => setVisibleCols(e.value as ConfigColKey[])}
        placeholder="Toggle columns"
        style={{ fontSize: '0.8rem' }}
      />
    </div>
  )

  const fmtDate = (iso: string | null) => iso ? new Date(iso).toLocaleString() : '—'

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

      <div className={styles.section}>
        <div className={styles.tableWrap}>
        <DataTable
          value={displayed}
          loading={loading}
          header={tableHeader}
          size="small"
          emptyMessage="No field configs yet."
          sortMode="single"
        >
          <Column
            field="name"
            header="Name"
            body={(cfg: FieldConfigOut) => <strong>{cfg.name}</strong>}
            sortable
          />
          {vis.has('description') && (
            <Column field="description" header="Description" sortable />
          )}
          {vis.has('languages') && (
            <Column
              header="Languages"
              body={(cfg: FieldConfigOut) => (
                <span className={styles.langGrid}>
                  {cfg.languages.map(l => (
                    <span key={l.code} className={`${styles.langTag} ${l.is_default ? styles.langTagDefault : ''}`}>
                      {l.code}
                    </span>
                  ))}
                </span>
              )}
            />
          )}
          {vis.has('entity_count') && (
            <Column field="entity_count" header="Entities" sortable />
          )}
          {vis.has('stale_entity_count') && (
            <Column field="stale_entity_count" header="Stale Entities" sortable />
          )}
          {vis.has('published_submodel_usage_count') && (
            <Column field="published_submodel_usage_count" header="Submodel Usages" sortable />
          )}
          {vis.has('type_count') && (
            <Column
              header="Types"
              body={(cfg: FieldConfigOut) => cfg.type_ids.length}
              sortable
              sortField="type_ids"
            />
          )}
          {vis.has('version_count') && (
            <Column field="version_count" header="Versions" sortable />
          )}
          {vis.has('created_at') && (
            <Column
              field="created_at"
              header="Created"
              body={(cfg: FieldConfigOut) => fmtDate(cfg.created_at)}
              sortable
            />
          )}
          {vis.has('last_published_at') && (
            <Column
              field="last_published_at"
              header="Last Published"
              body={(cfg: FieldConfigOut) => fmtDate(cfg.last_published_at)}
              sortable
            />
          )}
          <Column
            header="Actions"
            body={(cfg: FieldConfigOut) => (
              <div className={styles.tableActions}>
                <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                  onClick={() => navigate(`/udm-admin/configs/${cfg.id}`)}>
                  Open
                </button>
                <button type="button" className={`${styles.btn} ${styles.btnDanger}`}
                  onClick={() => void handleDelete(cfg.id, cfg.name)}>
                  Delete
                </button>
              </div>
            )}
          />
        </DataTable>
        </div>
      </div>
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

const ACTIONS = ['view', 'browse', 'create', 'save', 'transition', 'delete']

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
  const [nodes, setNodes] = useState<EvalNodeOut[]>([])
  const [nodeId, setNodeId] = useState('')
  const [sudo, setSudo] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<PolicyEvalOut | null>(null)
  const [evalError, setEvalError] = useState<string | null>(null)
  const [activeResultTab, setActiveResultTab] = useState<'output' | 'full_doc' | 'input' | 'policies' | 'prints'>('output')

  useEffect(() => {
    udmSearchEntities('', typeId).then(setEntities).catch(() => {})
    udmSearchUsers('').then(setUsers).catch(() => {})
  }, [typeId])

  // Node tree (incl. submodel nodes) for transition targeting
  useEffect(() => {
    setNodes([])
    setNodeId('')
    setTransitionName('')
    if (!entityId) return
    udmEvalPolicyNodes(typeId, entityId).then(ns => {
      setNodes(ns)
      setNodeId(ns.find(n => n.parent_id == null)?.id ?? '')
    }).catch(() => {})
  }, [typeId, entityId])

  const selectedNode = nodes.find(n => n.id === nodeId) ?? null
  const nodeTransitions = (selectedNode?.workflow_fields ?? []).flatMap(wf =>
    wf.transitions.map(t => ({ field: wf.slug, name: t })))

  async function handleRun() {
    if (!entityId || !userId) return
    setRunning(true)
    setResult(null)
    setEvalError(null)
    try {
      const out = await udmEvalPolicy(
        typeId, entityId, userId, action,
        action === 'transition' && transitionName ? transitionName : undefined,
        action === 'transition' && nodeId ? nodeId : undefined,
        sudo,
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
          <>
            <div className={styles.formGroup} style={{ minWidth: '180px' }}>
              <label className={styles.label}>Node</label>
              <select className={styles.select} value={nodeId}
                onChange={e => { setNodeId(e.target.value); setTransitionName('') }}>
                {nodes.length === 0 && <option value="">— select entity first —</option>}
                {nodes.map(n => (
                  <option key={n.id} value={n.id}>
                    {n.label} ({n.id.slice(0, 8)}…)
                  </option>
                ))}
              </select>
            </div>
            <div className={styles.formGroup} style={{ minWidth: '160px' }}>
              <label className={styles.label}>Transition</label>
              {nodeTransitions.length > 0 ? (
                <select className={styles.select} value={transitionName}
                  onChange={e => setTransitionName(e.target.value)}>
                  <option value="">— select transition —</option>
                  {nodeTransitions.map(t => (
                    <option key={`${t.field}:${t.name}`} value={t.name}>
                      {t.name} ({t.field})
                    </option>
                  ))}
                </select>
              ) : (
                <input className={styles.input} value={transitionName}
                  onChange={e => setTransitionName(e.target.value)} placeholder="submit" />
              )}
            </div>
          </>
        )}

        <div className={styles.formGroup} style={{ minWidth: '90px', flex: 'none', justifyContent: 'flex-end' }}>
          <label className={styles.label} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', cursor: 'pointer' }}>
            <input type="checkbox" checked={sudo} onChange={e => setSudo(e.target.checked)} />
            Sudo
          </label>
        </div>

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
                {Object.values((result.output.viewable_fields ?? {}) as Record<string, string[]>).flat().length} viewable fields
                {' · '}
                {Object.values((result.output.editable_fields ?? {}) as Record<string, string[]>).flat().length} editable fields
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

          {/* Field grants as a tree: node → fields, with per-field view/edit state */}
          {result.input_document?.entity != null && (
            <div style={{ marginBottom: '0.75rem' }}>
              <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#555', marginBottom: '0.25rem' }}>
                Field grants
                <span style={{ fontWeight: 400, marginLeft: '0.6rem', color: '#777' }}>
                  ✏ editable · 👁 view only · <s>hidden</s>
                </span>
              </div>
              <FieldGrantTree
                node={result.input_document.entity as GrantTreeNode}
                schemas={(result.input_document.schemas ?? {}) as Record<string, { slug?: string }>}
                viewable={(result.output.viewable_fields ?? {}) as Record<string, string[]>}
                editable={(result.output.editable_fields ?? {}) as Record<string, string[]>}
                label="root"
              />
            </div>
          )}

          {/* Detail tabs */}
          <div className={styles.tabs} style={{ marginBottom: '0.5rem' }}>
            {(['output', 'full_doc', 'input', 'policies', 'prints'] as const).map(tab => {
              if (tab === 'prints' && !(result.prints?.length)) return null
              if (tab === 'full_doc' && !result.full_document && !(result.rule_errors?.length)) return null
              return (
                <button key={tab} type="button"
                  className={`${styles.tab} ${activeResultTab === tab ? styles.tabActive : ''}`}
                  onClick={() => setActiveResultTab(tab)}>
                  {tab === 'output' ? 'Output'
                    : tab === 'full_doc'
                      ? `Full Document${result.rule_errors?.length ? ` (${result.rule_errors.length} errors)` : ''}`
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

          {activeResultTab === 'full_doc' && (
            <div>
              {result.rule_errors && result.rule_errors.length > 0 && (
                <div style={{ marginBottom: '0.5rem' }}>
                  {result.rule_errors.map((e, i) => (
                    <div key={i} style={{ fontSize: '0.85rem', color: '#dc2626', fontFamily: 'monospace', marginBottom: '0.2rem' }}>
                      {e}
                    </div>
                  ))}
                </div>
              )}
              {result.full_document
                ? <pre className={styles.monoText} style={{ margin: 0, padding: '0.75rem', background: '#f8f8f8', borderRadius: '6px', overflow: 'auto', maxHeight: '600px', fontSize: '0.8rem', border: '1px solid #e0e0e0' }}>
                    {JSON.stringify(result.full_document, null, 2)}
                  </pre>
                : <div className={styles.emptyState}>Full document unavailable — check debug log for raw value.</div>
              }
            </div>
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
  onDeleted: () => void
  allConfigs: FieldConfigOut[]
  allPolicies: PolicyOut[]
  onUpdated: (t: UDMTypeOut) => void
  isSuperuser: boolean
}

function TypeDetail({ udmType, onBack, onDeleted, allConfigs, allPolicies, onUpdated, isSuperuser }: TypeDetailProps) {
  const [policies, setPolicies] = useState<PolicyOut[]>([])
  const [assignSlug, setAssignSlug] = useState('')
  const [configId, setConfigId] = useState(udmType.field_config_id ?? '')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [editingMeta, setEditingMeta] = useState(false)
  const [editName, setEditName] = useState(udmType.name)
  const [editLabel, setEditLabel] = useState(udmType.label)
  const { i18n } = useTranslation()
  const [typeDescriptions, setTypeDescriptions] = useState<Record<string, string>>({})

  useEffect(() => {
    udmGetTypeDescriptions(udmType.id.toString()).then(setTypeDescriptions).catch(() => {})
  }, [udmType.id])

  const uiLang = i18n.language.split('-')[0]
  const typeDescription = typeDescriptions[uiLang] ?? typeDescriptions[''] ?? Object.values(typeDescriptions)[0] ?? ''

  const loadPolicies = useCallback(async () => {
    try {
      const list = await udmListTypePolicies(udmType.id)
      setPolicies(list)
    } catch { /* ignore */ }
  }, [udmType.id])

  useEffect(() => { void loadPolicies() }, [loadPolicies])

  async function handleSaveMeta() {
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const updated = await udmUpdateTypeMeta(udmType.id.toString(), {
        name: editName.trim() || undefined,
        label: editLabel,
      })
      onUpdated(updated)
      setEditingMeta(false)
      setSuccess('Saved.')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

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

  async function handleDelete() {
    if (!window.confirm(`Delete UDM Type "${udmType.name}"? This cannot be undone.`)) return
    setDeleting(true)
    setError(null)
    try {
      await udmDeleteType(udmType.id)
      onDeleted()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
      setDeleting(false)
    }
  }

  return (
    <div>
      <div className={styles.section}>
        <div className={styles.sectionTitle} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Type Info</span>
          {!editingMeta && (
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={() => { setEditingMeta(true); setEditName(udmType.name); setEditLabel(udmType.label) }}>
              Edit
            </button>
          )}
        </div>
        {editingMeta ? (
          <>
            <div className={styles.row}>
              <div className={styles.formGroup}>
                <label className={styles.label}>Name *</label>
                <input className={styles.input} value={editName} onChange={e => setEditName(e.target.value)} />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.label}>Label (display name)</label>
                <input className={styles.input} value={editLabel} onChange={e => setEditLabel(e.target.value)} />
              </div>
            </div>
            {error && <div className={styles.error}>{error}</div>}
            {success && <div className={styles.success}>{success}</div>}
            <div className={styles.row} style={{ marginTop: '0.5rem' }}>
              <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
                onClick={() => void handleSaveMeta()} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                onClick={() => setEditingMeta(false)}>
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            {udmType.label && <div><strong>Label:</strong> {udmType.label}</div>}
            {typeDescription
              ? <div style={{ marginTop: '0.5rem' }}><UdfMarkdown content={typeDescription} /></div>
              : <div style={{ color: '#888', fontSize: '0.875rem' }}>No description (define TYPE_DESCRIPTION in policy).</div>}
          </>
        )}
      </div>
      <div className={styles.detailHeader}>
        <button type="button" className={styles.backBtn} onClick={onBack}>← Back to Types</button>
        <h2 className={styles.pageTitle} style={{ margin: 0 }}>{udmType.name}</h2>
        <button type="button" className={`${styles.btn} ${styles.btnDanger}`}
          onClick={() => void handleDelete()} disabled={deleting}>
          {deleting ? 'Deleting…' : 'Delete Type'}
        </button>
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
          <div className={styles.tableWrap}>
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
          </div>
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
  const [newLabel, setNewLabel] = useState('')
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
      const t = await udmCreateType({ name: newName.trim(), label: newLabel.trim() })
      setTypes(prev => [...prev, t])
      setNewName('')
      setNewLabel('')
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
        onDeleted={() => { setSelectedType(null); void loadAll() }}
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
              <label className={styles.label}>Name * (internal identifier)</label>
              <input className={styles.input} value={newName} onChange={e => setNewName(e.target.value)}
                placeholder="workshop" />
            </div>
            <div className={styles.formGroup}>
              <label className={styles.label}>Label (display name)</label>
              <input className={styles.input} value={newLabel} onChange={e => setNewLabel(e.target.value)}
                placeholder="Workshop" />
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
                <th>Label</th>
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
                    <td>{t.label || <span style={{ color: '#999' }}>—</span>}</td>
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

const VALID_TABS = new Set<AdminTab>(['configs', 'types', 'policies', 'migrations', 'bundle', 'workflow'])

export function UdmAdminPage() {
  useTranslation()
  const { tab: tabParam, configId: configIdParam } = useParams<{ tab?: string; configId?: string }>()
  const tab: AdminTab = (tabParam && VALID_TABS.has(tabParam as AdminTab))
    ? (tabParam as AdminTab)
    : 'configs'

  return (
    <div className={styles.page}>
      {tab === 'configs' && <ConfigsTab selectedConfigId={configIdParam ?? null} />}
      {tab === 'types' && <TypesTab />}
      {tab === 'policies' && <PoliciesTab />}
      {tab === 'migrations' && <BulkMigrationTab />}
      {tab === 'bundle' && <BundleTab />}
      {tab === 'workflow' && <WorkflowEditor />}
    </div>
  )
}

// ── Split editors: Data Fields + Form Config (PLAN_split_form_tree_and_data_fields.md) ──

interface ConfigDraftEditorProps {
  configId: string
  languages: string[]
  allConfigs: FieldConfigOut[]
  onSaved: (v: ConfigVersionOut) => void
}

/** Loads the draft once, holds data_fields + form_elements + notes in state,
 *  and renders two editors (Data Fields / Form Config) with shared Save/Publish. */
function ConfigDraftEditor({ configId, languages, allConfigs, onSaved }: ConfigDraftEditorProps) {
  const [draft, setDraft] = useState<ConfigVersionOut | null>(null)
  const [notes, setNotes] = useState('')
  const [dataFields, setDataFields] = useState<FieldDefinitionIn[]>([])
  const [formElements, setFormElements] = useState<FormElementIn[]>([])
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [success, setSuccess] = useState<string | null>(null)
  const [subTab, setSubTab] = useState<'data' | 'form' | 'preview'>('data')

  const load = useCallback(async () => {
    try {
      const v = await udmGetDraftVersion(configId)
      setDraft(v)
      setNotes(v.notes)
      setDataFields(v.data_fields.map(dfOutToIn))
      setFormElements(v.form_elements.map(elOutToIn))
    } catch {
      setDraft(null)
      setDataFields([])
      setFormElements([])
    }
  }, [configId])

  useEffect(() => { void load() }, [load])

  function dfOutToIn(fd: FieldDefinitionOut): FieldDefinitionIn {
    return {
      slug: fd.slug,
      data_type: fd.data_type as DataType,
      is_localized: fd.is_localized,
      type_config: fd.type_config as Record<string, unknown>,
      default: fd.default ?? null,
      submodel_config_version_id: fd.submodel_config?.version_id ?? null,
      workflow_version_id: (fd as FieldDefinitionOut & { workflow_version?: { id?: string } }).workflow_version?.id ?? null,
      // legacy form-tree fields are ignored on the data-field side
      sort_order: 0, is_preview: false, parent_slug: null,
      labels: null, help_texts: {},
    }
  }

  function elOutToIn(el: FormElementOut): FormElementIn {
    // Coerce empty label/help_text dicts to null — the backend's LocalizedLabel
    // requires min_length=1, so {} is rejected. null means "no labels".
    const labelDict = el.label as Record<string, string> | undefined
    const hasLabels = labelDict && Object.keys(labelDict).length > 0
      && Object.values(labelDict).some(v => (v ?? '').trim() !== '')
    const helpDict = el.help_text as Record<string, string> | undefined
    const hasHelp = helpDict && Object.keys(helpDict).length > 0
    return {
      slug: el.slug,
      element_type: el.element_type,
      parent_slug: el.parent_slug ?? null,
      sort_order: el.sort_order,
      is_preview: el.is_preview,
      labels: hasLabels ? labelDict! : null,
      help_texts: hasHelp ? helpDict! : {},
      type_config: el.type_config as Record<string, unknown>,
      bindings: (el.bindings ?? []).map(b => ({ data_field_slug: b.data_field_slug, role: b.role })),
    }
  }

  async function handleSave() {
    setSaving(true)
    setErrors([])
    setSuccess(null)
    try {
      const v = await udmReplaceDraft(configId, { notes, data_fields: dataFields, form_elements: formElements })
      setDraft(v)
      setDataFields(v.data_fields.map(dfOutToIn))
      setFormElements(v.form_elements.map(elOutToIn))
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

  if (!draft) return <div className={styles.emptyState}>Loading draft…</div>

  return (
    <div>
      <div className={styles.subsectionTitle}>Draft Version</div>
      <div className={styles.row}>
        <div className={styles.formGroup} style={{ flex: 2 }}>
          <label className={styles.label}>Change Notes</label>
          <input className={styles.input} value={notes}
            onChange={e => setNotes(e.target.value)} placeholder="Notes for this version" />
        </div>
      </div>

      {/* Sub-tab switch */}
      <div style={{ display: 'flex', gap: '0.5rem', margin: '1rem 0 0.8rem', borderBottom: '2px solid #e2e8f0' }}>
        {(['data', 'form', 'preview'] as const).map(st => (
          <button key={st} type="button"
            className={`${styles.btn} ${subTab === st ? styles.btnPrimary : styles.btnSecondary}`}
            style={{
              fontSize: '0.9rem', fontWeight: 600, padding: '0.45rem 0.9rem',
              borderBottom: subTab === st ? '3px solid #2563eb' : '3px solid transparent',
              borderRadius: '4px 4px 0 0', marginBottom: '-2px',
            }}
            onClick={() => setSubTab(st)}>
            {st === 'data' ? '📋 Data Fields' : st === 'form' ? '🎨 Form Config' : '👁 Preview Config'}
          </button>
        ))}
      </div>
      <div style={{ fontSize: '0.75rem', color: '#94a3b8', margin: '-0.4rem 0 0.8rem' }}>
        {subTab === 'data'
          ? 'Define storage: field types, localization, defaults, validation. Not shown in the form until bound (Form Config tab).'
          : subTab === 'form'
          ? 'Build the form tree: drag data fields and widgets from the right sidebar into the tree.'
          : 'Build the preview tree: elements here (is_preview=true) define how an entity is summarized in lists/cards. Stored like a form, separate from the main form tree.'}
      </div>

      {subTab === 'data' ? (
        <DataFieldsEditor
          dataFields={dataFields}
          onChange={setDataFields}
          languages={languages}
          allConfigs={allConfigs}
          formElements={formElements}
        />
      ) : subTab === 'form' ? (
        <FormConfigEditor
          formElements={formElements.filter(e => !e.is_preview)}
          onChange={els => setFormElements([...els, ...formElements.filter(e => e.is_preview)])}
          dataFields={dataFields}
          languages={languages}
        />
      ) : (
        <FormConfigEditor
          formElements={formElements.filter(e => e.is_preview)}
          onChange={els => setFormElements([...formElements.filter(e => !e.is_preview), ...els.map(e => ({ ...e, is_preview: true }))])}
          dataFields={dataFields}
          languages={languages}
          isPreview
        />
      )}

      {errors.length > 0 && (
        <div className={styles.error} style={{ marginTop: '0.75rem' }}>
          {errors.map((msg, i) => <div key={i}>{msg}</div>)}
        </div>
      )}
      {success && <div className={styles.success} style={{ marginTop: '0.75rem' }}>{success}</div>}

      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
        <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save Draft'}
        </button>
        <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={handlePublish} disabled={publishing}>
          {publishing ? 'Publishing…' : 'Publish'}
        </button>
      </div>
    </div>
  )
}

// ── Data Fields editor (storage semantics) ─────────────────────────────────────

interface DataFieldsEditorProps {
  dataFields: FieldDefinitionIn[]
  onChange: (fields: FieldDefinitionIn[]) => void
  languages: string[]
  allConfigs: FieldConfigOut[]
  formElements: FormElementIn[]
}

// Value-bearing data field types grouped for the sidebar (structural types excluded).
const DATA_FIELD_CATEGORIES: { label: string; types: DataType[] }[] = [
  { label: 'Text', types: ['text_short', 'text_long', 'text_markdown', 'text_richtext'] },
  { label: 'Number', types: ['integer', 'float'] },
  { label: 'Date & Time', types: ['date', 'time', 'datetime'] },
  { label: 'Choice', types: ['select_single', 'select_multi'] },
  { label: 'Reference', types: ['user_select', 'user_select_multi', 'group_select', 'group_select_multi', 'entity_select', 'entity_select_multi', 'submodel_select', 'submodel_list'] },
  { label: 'File', types: ['image', 'file'] },
  { label: 'Special', types: ['boolean', 'slug_id', 'workflow'] },
]

function DataFieldsEditor({ dataFields, onChange, languages, allConfigs, formElements }: DataFieldsEditorProps) {
  const [editingSlug, setEditingSlug] = useState<string | null>(null)
  const [dragType, setDragType] = useState<DataType | null>(null)
  const [dropIndex, setDropIndex] = useState<number | null>(null)

  // Data fields referenced by at least one FormElement binding.
  const referencedSlugs = new Set<string>()
  for (const el of formElements) {
    for (const b of el.bindings ?? []) referencedSlugs.add(b.data_field_slug)
  }

  function addDataFieldOfType(dt: DataType, atIndex?: number) {
    const f: FieldDefinitionIn = {
      slug: '', data_type: dt, is_localized: false, is_preview: false,
      labels: null, help_texts: {}, type_config: {}, default: null,
    }
    if (atIndex === undefined) {
      onChange([...dataFields, f])
    } else {
      const next = [...dataFields]
      next.splice(atIndex, 0, f)
      onChange(next)
    }
    setEditingSlug('')
  }

  function addDataField() {
    addDataFieldOfType('text_short')
  }

  function updateField(slug: string, updated: FieldDefinitionIn) {
    onChange(dataFields.map(f => (f.slug === slug ? updated : f)))
    if (slug !== updated.slug) setEditingSlug(updated.slug)
  }

  function removeField(slug: string) {
    onChange(dataFields.filter(f => f.slug !== slug))
  }

  function handleAreaDrop(e: React.DragEvent) {
    e.preventDefault()
    if (dragType) {
      // If a drop line was hovered, insert there; otherwise append to end.
      addDataFieldOfType(dragType, dropIndex ?? undefined)
      setDragType(null)
    }
    setDropIndex(null)
  }

  function handleListLineDrop(e: React.DragEvent, index: number) {
    e.preventDefault()
    e.stopPropagation()
    if (dragType) {
      addDataFieldOfType(dragType, index)
      setDragType(null)
    }
    setDropIndex(null)
  }

  return (
    <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
      {/* Field list (left) */}
      <div
        style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '0.6rem' }}
        onDragOver={e => { if (dragType) e.preventDefault() }}
        onDrop={handleAreaDrop}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span className={styles.subsectionTitle}>Data Fields ({dataFields.length})</span>
          <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} style={{ fontSize: '0.78rem' }}
            onClick={addDataField}>+ Data Field</button>
        </div>
        <div style={{ fontSize: '0.73rem', color: '#94a3b8' }}>
          Data fields define storage semantics (type, localization, defaults, validation). Drag a type from the right sidebar, or click + Data Field. Not shown in the form until bound (Form Config tab).
        </div>
        {dataFields.length === 0 && !dragType && (
          <div className={styles.emptyState}>No data fields yet. Drag a field type from the right sidebar.</div>
        )}
        {dataFields.length === 0 && dragType && (
          <DropLine depth={0} active={dropIndex === 0}
            onDragOver={e => { e.preventDefault(); setDropIndex(0) }}
            onDrop={e => handleListLineDrop(e, 0)} />
        )}
        {dataFields.map((f, i) => (
          <div key={`df-${i}`}>
            {dragType && (
              <DropLine depth={0} active={dropIndex === i}
                onDragOver={e => { e.preventDefault(); setDropIndex(i) }}
                onDrop={e => handleListLineDrop(e, i)} />
            )}
            <div className={styles.card}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <span style={{
                background: '#f3f4f6', color: '#374151',
                fontSize: '0.65rem', fontWeight: 600, padding: '0.1rem 0.4rem', borderRadius: '3px',
              }}>{f.data_type}</span>
              {f.slug && !referencedSlugs.has(f.slug) && (
                <span title="Not bound to any form element (hidden in the form)"
                  style={{
                    background: '#fef3c7', color: '#92400e',
                    fontSize: '0.6rem', fontWeight: 600, padding: '0.08rem 0.35rem', borderRadius: '3px',
                    border: '1px solid #fcd34d',
                  }}>⚠ unreferenced</span>
              )}
              <span style={{ fontFamily: 'monospace', fontSize: '0.85rem', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {f.slug || <em style={{ color: '#bbb' }}>new</em>}
              </span>
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
                style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }}
                onClick={() => setEditingSlug(editingSlug === (f.slug || '') ? null : (f.slug || ''))}>
                {editingSlug === (f.slug || '') ? '✕' : '✎'}
              </button>
              <button type="button" className={`${styles.btn} ${styles.btnDanger}`}
                style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }}
                onClick={() => removeField(f.slug)}>✕</button>
            </div>
            {editingSlug === (f.slug || '') && (
              <div style={{ marginTop: '0.5rem', padding: '0.6rem', background: '#f8fafc', borderRadius: '4px', border: '1px solid #e2e8f0' }}>
                <FieldEditor
                  field={f}
                  onChange={updated => updateField(f.slug, updated)}
                  onRemove={() => removeField(f.slug)}
                  languages={languages}
                allConfigs={allConfigs}
                noHeader
              />
            </div>
            )}
            </div>
            {dragType && (
              <DropLine depth={0} active={dropIndex === i + 1}
                onDragOver={e => { e.preventDefault(); setDropIndex(i + 1) }}
                onDrop={e => handleListLineDrop(e, i + 1)} />
            )}
          </div>
        ))}
      </div>

      {/* Sidebar (right): field type library */}
      <div className={styles.sidebar}>
        <div className={styles.subsectionTitle} style={{ fontSize: '0.85rem' }}>Field Types</div>
        <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Drag onto the list, or click to add →</div>
        {DATA_FIELD_CATEGORIES.map(cat => (
          <div key={cat.label}>
            <div style={{ fontWeight: 600, fontSize: '0.72rem', color: '#475569', margin: '0.5rem 0 0.2rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
              {cat.label}
            </div>
            {cat.types.map(t => (
              <div key={t}
                draggable
                onDragStart={() => setDragType(t)}
                onDragEnd={() => { setDragType(null); setDropIndex(null) }}
                onClick={() => addDataFieldOfType(t)}
                title={`Add a ${t} data field`}
                style={{
                  padding: '0.3rem 0.4rem', margin: '0.2rem 0', background: '#fff',
                  border: '1px solid #e2e8f0', borderRadius: '4px', cursor: 'grab',
                  fontSize: '0.78rem', fontFamily: 'monospace',
                }}>
                {t}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Form Config editor (form tree + sidebar drag-drop) ──────────────────────────

interface FormConfigEditorProps {
  formElements: FormElementIn[]
  onChange: (els: FormElementIn[]) => void
  dataFields: FieldDefinitionIn[]
  languages: string[]
  /** When true, new elements created in this editor are marked is_preview=true
   * (used by the Preview Config tab, which edits a separate is_preview tree). */
  isPreview?: boolean
}

const STRUCTURAL_ELEMENT_TYPES = ['tab_container', 'tab', 'save_button', 'hstack', 'hstack_group', 'tab_prev', 'tab_next'] as const
const PARENT_ELEMENT_TYPES = new Set(['tab_container', 'tab', 'hstack', 'hstack_group'])
const WIDGET_ELEMENT_TYPES = ['field', 'date_range'] as const

/** Binding roles preset per widget element type. Each entry lists the roles a
 * binding of that element must use (in order). The user picks the bound data
 * field for each role from a dropdown — no freetext roles. */
const BINDING_ROLES: Record<string, string[]> = {
  field: [''],
  date_range: ['from', 'to'],
}

interface ElNodeData { el: FormElementIn }
type ElTreeNode = TreeNode & { data?: ElNodeData }

/** A "drop here" line rendered between elements. `path` describes the insertion
 * point as a list of child indices (e.g. [] = root end, [1] = after root[1],
 * [0,2] = inside root[0] after its 2nd child). `onDrop` is called with the path. */
function DropLine({ depth, active, onDragOver, onDrop }: {
  depth: number
  active: boolean
  onDragOver: (e: React.DragEvent) => void
  onDrop: (e: React.DragEvent) => void
}) {
  return (
    <div
      className={`${styles.dropLine} ${active ? styles.over : ''}`}
      style={{ marginLeft: depth * 1.4 }}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      {active && <span>drop here</span>}
    </div>
  )
}

function FormConfigEditor({ formElements, onChange, dataFields, languages, isPreview = false }: FormConfigEditorProps) {
  const [nodes, setNodes] = useState<ElTreeNode[]>([])
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({})
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [dragKind, setDragKind] = useState<{ kind: 'datafield' | 'widget'; slug?: string; elementType?: string } | null>(null)
  const [dragOverKey, setDragOverKey] = useState<string | null>(null)
  // Track the last formElements array we emitted via onChange. The rebuild
  // effect skips when the incoming prop is our own echo, so typing in the
  // inline editor doesn't tear down the tree and lose focus.
  const lastEmittedRef = useRef<FormElementIn[] | null>(null)

  // Structural signature: only slug/parent/type/order matter. Field *values*
  // (labels, help_texts, bindings) are intentionally excluded so that editing
  // them in the inline editor (which echoes back through onChange) does NOT
  // trigger a tree rebuild that would unmount the input and steal focus.
  function structSig(els: FormElementIn[]): string {
    return els.map(e => `${e.slug}|${e.parent_slug ?? ''}|${e.element_type}|${e.sort_order}`).join(';')
  }

  // Rebuild the tree when the incoming formElements structure changes from an
  // EXTERNAL source (load/save, add/remove/reorder). Skips value-only echoes
  // to keep the inline editor mounted and focused while the user types.
  useEffect(() => {
    if (formElements === lastEmittedRef.current) return
    // Same structure as the current tree? Skip the rebuild (only field values
    // changed, which the inline editor already holds in local node state).
    const currentEls = nodesToEls(nodes)
    if (structSig(formElements) === structSig(currentEls)) {
      lastEmittedRef.current = formElements
      return
    }
    const ns = elsToNodes(formElements)
    setNodes(ns)
    lastEmittedRef.current = formElements
    // Auto-expand all parent nodes so the tree is open by default.
    const exp: Record<string, boolean> = {}
    function walk(list: ElTreeNode[]) {
      for (const n of list) {
        const el = (n.data as ElNodeData)!.el
        if (PARENT_ELEMENT_TYPES.has(el.element_type)) exp[n.key as string] = true
        if (n.children) walk(n.children as ElTreeNode[])
      }
    }
    walk(ns)
    setExpandedKeys(exp)
  }, [formElements]) // eslint-disable-line react-hooks/exhaustive-deps

  // Propagate tree changes up. Record the emitted array so the rebuild
  // effect recognizes it as our own echo and skips (preserves focus).
  function commit(ns: ElTreeNode[]) {
    setNodes(ns)
    const els = nodesToEls(ns)
    lastEmittedRef.current = els
    onChange(els)
  }

  function elsToNodes(els: FormElementIn[]): ElTreeNode[] {
    const roots: ElTreeNode[] = []
    const made: Record<string, ElTreeNode> = {}
    // First pass: create nodes keyed by slug (stable identity across edits,
    // so typing in the inline editor doesn't unmount/remount the input and
    // lose focus). A slug is unique within a form tree.
    for (const el of els) {
      const n: ElTreeNode = {
        key: el.slug,
        label: el.slug,
        data: { el },
        leaf: !PARENT_ELEMENT_TYPES.has(el.element_type),
        draggable: el.element_type !== 'tab_container',
        droppable: PARENT_ELEMENT_TYPES.has(el.element_type),
        children: PARENT_ELEMENT_TYPES.has(el.element_type) ? [] : undefined,
      }
      made[el.slug] = n
    }
    // Second pass: link parents
    for (const el of els) {
      const n = made[el.slug]
      if (el.parent_slug && made[el.parent_slug]) {
        const parent = made[el.parent_slug]
        ;(parent.children ?? (parent.children = [])).push(n)
      } else {
        roots.push(n)
      }
    }
    return roots
  }

  function nodesToEls(ns: ElTreeNode[]): FormElementIn[] {
    const out: FormElementIn[] = []
    function walk(list: ElTreeNode[], parentSlug: string | null) {
      list.forEach((n, i) => {
        const el = (n.data as ElNodeData)!.el
        out.push({ ...el, parent_slug: parentSlug, sort_order: i })
      })
      for (const n of list) {
        const el = (n.data as ElNodeData)!.el
        if (n.children?.length) walk(n.children as ElTreeNode[], el.slug)
      }
    }
    walk(ns, null)
    return out
  }

  function updateElNodeByKey(ns: ElTreeNode[], key: string, updated: FormElementIn): ElTreeNode[] {
    return ns.map(n => {
      if (n.key === key) {
        const rebuilt: ElTreeNode = {
          ...n, label: updated.slug, data: { el: updated },
          leaf: !PARENT_ELEMENT_TYPES.has(updated.element_type),
          draggable: updated.element_type !== 'tab_container',
          droppable: PARENT_ELEMENT_TYPES.has(updated.element_type),
        }
        return rebuilt
      }
      if (n.children) return { ...n, children: updateElNodeByKey(n.children as ElTreeNode[], key, updated) }
      return n
    })
  }

  function removeElNodeByKey(ns: ElTreeNode[], key: string): ElTreeNode[] {
    return ns
      .filter(n => n.key !== key)
      .map(n => (n.children ? { ...n, children: removeElNodeByKey(n.children as ElTreeNode[], key) } : n))
  }

  /** Find the [rootIndex, childIndex...] path of a node by key, or null. */
  function pathOfKey(ns: ElTreeNode[], key: string): number[] | null {
    for (let i = 0; i < ns.length; i++) {
      if (ns[i].key === key) return [i]
      if (ns[i].children) {
        const sub = pathOfKey(ns[i].children as ElTreeNode[], key)
        if (sub) return [i, ...sub]
      }
    }
    return null
  }

  /** Insert a node before the node with the given key (same parent). */
  function insertBeforeKey(ns: ElTreeNode[], key: string, node: ElTreeNode): ElTreeNode[] {
    const path = pathOfKey(ns, key)
    if (!path) return [...ns, node]
    const parentPath = path.slice(0, -1)
    const idx = path[path.length - 1]
    return insertAtPathKey(ns, [...parentPath, idx], node)
  }

  /** Insert a node after the node with the given key (same parent). */
  function insertAfterKey(ns: ElTreeNode[], key: string, node: ElTreeNode): ElTreeNode[] {
    const path = pathOfKey(ns, key)
    if (!path) return [...ns, node]
    const parentPath = path.slice(0, -1)
    const idx = path[path.length - 1]
    return insertAtPathKey(ns, [...parentPath, idx + 1], node)
  }

  /** Insert at a numeric index path (helper for insertBefore/AfterKey). */
  function insertAtPathKey(ns: ElTreeNode[], path: number[], node: ElTreeNode): ElTreeNode[] {
    if (path.length === 0) return [...ns, node]
    const [head, ...rest] = path
    if (rest.length === 0) {
      const out = [...ns]
      out.splice(head, 0, node)
      return out
    }
    return ns.map((n, i) => {
      if (i === head && n.children) {
        return { ...n, children: insertAtPathKey(n.children as ElTreeNode[], rest, node) }
      }
      return n
    })
  }

  function makeElementNode(elementType: string, slugBase: string, bindings: FormElementIn['bindings'] = []): ElTreeNode {
    const usedSlugs = new Set(nodesToEls(nodes).map(e => e.slug))
    let slug = slugBase, n = 1
    while (usedSlugs.has(slug)) { slug = `${slugBase}-${++n}` }
    // Labels start null (user fills them via the inline editor). The backend
    // LocalizedLabel requires min_length=1 with non-empty values, so an empty
    // dict would be rejected on save.
    const el: FormElementIn = {
      slug, element_type: elementType,
      sort_order: 0, is_preview: isPreview,
      labels: null,
      help_texts: {}, type_config: {}, bindings,
    }
    return {
      key: slug, label: slug, data: { el },
      leaf: !PARENT_ELEMENT_TYPES.has(elementType),
      draggable: elementType !== 'tab_container',
      droppable: PARENT_ELEMENT_TYPES.has(elementType),
      children: PARENT_ELEMENT_TYPES.has(elementType) ? [] : undefined,
    }
  }

  // Sidebar data-field lists: unreferenced first, then referenced.
  const referencedSlugs = new Set<string>()
  for (const el of nodesToEls(nodes)) {
    for (const b of el.bindings ?? []) referencedSlugs.add(b.data_field_slug)
  }
  const unreferencedDataFields = dataFields.filter(f => !referencedSlugs.has(f.slug))
  const referencedDataFields = dataFields.filter(f => referencedSlugs.has(f.slug))

  function handleDragStart(e: React.DragEvent, kind: 'datafield' | 'widget', id: string) {
    setDragKind({ kind, slug: kind === 'datafield' ? id : undefined, elementType: kind === 'widget' ? id : undefined })
    e.dataTransfer.effectAllowed = 'copy'
    // PrimeReact Tree drop is HTML5 DnD; setting data lets the Tree's drop zone accept.
    e.dataTransfer.setData('text/plain', id)
  }

  function makeNodeFromDrag(d: { kind: 'datafield' | 'widget'; slug?: string; elementType?: string }): ElTreeNode | null {
    if (d.kind === 'datafield' && d.slug) {
      // Dragging a data field in → create a 'field' element bound to it.
      const df = dataFields.find(f => f.slug === d.slug)
      if (!df) return null
      return makeElementNode('field', df.slug, [{ data_field_slug: df.slug, role: '' }])
    }
    if (d.kind === 'widget' && d.elementType) {
      if (d.elementType === 'field') return makeElementNode('field', 'field')
      if (d.elementType === 'date_range') return makeElementNode('date_range', 'date-range')
      // structural
      return makeElementNode(d.elementType, d.elementType.replace(/_/g, '-'))
    }
    return null
  }

  function nodeTemplate(node: ElTreeNode) {
    const el = (node.data as ElNodeData)?.el
    if (!el) return <span>{node.label}</span>

    const isStructural = (STRUCTURAL_ELEMENT_TYPES as readonly string[]).includes(el.element_type)
    const isEditing = editingKey === node.key
    const typeColors: Record<string, { bg: string; color: string }> = {
      tab_container: { bg: '#dbeafe', color: '#1e40af' },
      tab: { bg: '#e0f2fe', color: '#075985' },
      hstack: { bg: '#fef9c3', color: '#854d0e' },
      hstack_group: { bg: '#fef3c7', color: '#92400e' },
      save_button: { bg: '#dcfce7', color: '#166534' },
      tab_prev: { bg: '#f3e8ff', color: '#6b21a8' },
      tab_next: { bg: '#f3e8ff', color: '#6b21a8' },
      field: { bg: '#f3f4f6', color: '#374151' },
      date_range: { bg: '#fae8ff', color: '#86198f' },
    }
    const colors = typeColors[el.element_type] ?? { bg: '#f3f4f6', color: '#374151' }
    const bindingSlugs = (el.bindings ?? []).map(b => b.data_field_slug).join(', ')
    const key = node.key as string
    const isParent = PARENT_ELEMENT_TYPES.has(el.element_type)

    // Warning badges: missing labels, or missing help-text translations.
    const labelDict = el.labels as Record<string, string> | null
    const hasAnyLabel = !!labelDict && Object.values(labelDict).some(v => (v ?? '').trim() !== '')
    const helpDict = el.help_texts as Record<string, string> | null
    const helpLangs = helpDict ? Object.keys(helpDict).filter(l => (helpDict[l] ?? '').trim() !== '') : []
    const hasAnyHelp = helpLangs.length > 0
    const missingHelpLangs = hasAnyHelp ? languages.filter(l => !helpLangs.includes(l)) : []
    const missingLabel = !isStructural && !hasAnyLabel
    const missingHelp = !isStructural && hasAnyHelp && missingHelpLangs.length > 0
    const warnings: string[] = []
    if (missingLabel) warnings.push('missing label')
    if (missingHelp) warnings.push(`missing help translation: ${missingHelpLangs.join(', ')}`)

    const dropLine = (position: 'before' | 'after') => {
      if (!dragKind) return null
      const overKey = `${position}:${key}`
      return (
        <div
          key={overKey}
          className={`${styles.dropLine} ${dragOverKey === overKey ? styles.over : ''}`}
          onDragOver={e => { e.preventDefault(); e.stopPropagation(); setDragOverKey(overKey) }}
          onDrop={e => {
            e.preventDefault(); e.stopPropagation()
            if (!dragKind) { setDragOverKey(null); return }
            const nn = makeNodeFromDrag(dragKind)
            if (nn) commit(position === 'before' ? insertBeforeKey(nodes, key, nn) : insertAfterKey(nodes, key, nn))
            setDragKind(null); setDragOverKey(null)
          }}
        >
          {dragOverKey === overKey && <span style={{ fontSize: '0.68rem', color: '#2563eb' }}>drop here</span>}
        </div>
      )
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
        {dropLine('before')}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flex: 1 }}>
          <span style={{
            background: colors.bg, color: colors.color,
            fontSize: '0.65rem', fontWeight: 600, padding: '0.1rem 0.4rem', borderRadius: '3px', flexShrink: 0,
          }}>{el.element_type}</span>
          {warnings.length > 0 && (
            <span style={{
              background: '#fef3c7', color: '#b45309',
              fontSize: '0.62rem', fontWeight: 700, padding: '0.08rem 0.35rem',
              borderRadius: '3px', flexShrink: 0, cursor: 'help',
            }} title={warnings.join('; ')}>
              ⚠
            </span>
          )}
          <span style={{ fontWeight: 500, fontSize: '0.85rem', fontFamily: isStructural ? 'inherit' : 'monospace', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {el.slug}
            {bindingSlugs && <span style={{ fontWeight: 400, color: '#666', marginLeft: '0.4rem' }}>→ {bindingSlugs}</span>}
          </span>
          {isParent && (
            <span style={{ fontSize: '0.65rem', color: '#94a3b8' }} title="Drop into to add as a child">
              ◦ child
            </span>
          )}
          <div style={{ display: 'flex', gap: '0.2rem', flexShrink: 0 }}>
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
              style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }}
              onClick={e => { e.stopPropagation(); setEditingKey(isEditing ? null : key) }}>
              {isEditing ? '✕' : '✎'}
            </button>
            <button type="button" className={`${styles.btn} ${styles.btnDanger}`}
              style={{ padding: '0.08rem 0.35rem', fontSize: '0.7rem' }}
              onClick={e => { e.stopPropagation(); commit(removeElNodeByKey(nodes, key)) }}>✕</button>
          </div>
        </div>
        {isEditing && (
          <div style={{ marginTop: '0.4rem', padding: '0.6rem', background: '#f8fafc', borderRadius: '4px', border: '1px solid #e2e8f0' }}
            onClick={e => e.stopPropagation()}>
            <FormElementEditor
              el={el}
              dataFields={dataFields}
              languages={languages}
              onChange={updated => commit(updateElNodeByKey(nodes, key, updated))}
            />
          </div>
        )}
        {dropLine('after')}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
      {/* Tree (left) */}
      <div
        style={{ flex: 1, minWidth: 0 }}
        onDragOver={e => { if (dragKind) e.preventDefault() }}
        onDrop={e => {
          e.preventDefault()
          if (!dragKind) { setDragOverKey(null); return }
          // Insert at the last-hovered drop-line position if known, else root.
          const nn = makeNodeFromDrag(dragKind)
          if (nn) {
            if (dragOverKey) {
              const [pos, key] = dragOverKey.split(':') as [string, string]
              commit(pos === 'before' ? insertBeforeKey(nodes, key, nn) : insertAfterKey(nodes, key, nn))
            } else {
              commit([...nodes, nn])
            }
          }
          setDragKind(null); setDragOverKey(null)
        }}
      >
        <div style={{ fontSize: '0.73rem', color: '#94a3b8', marginBottom: '0.4rem' }}>
          Drag fields from the sidebar →. Drop on a “drop here” line to insert at that point. Click ✎ to edit bindings/labels.
        </div>
        {nodes.length === 0 && (
          <div
            className={styles.emptyState}
            onDragOver={e => { if (dragKind) e.preventDefault() }}
            onDrop={e => {
              e.preventDefault()
              if (dragKind) { const nn = makeNodeFromDrag(dragKind); if (nn) commit([...nodes, nn]); setDragKind(null); setDragOverKey(null) }
            }}
          >No form elements yet. Drag a data field or a widget from the right sidebar.</div>
        )}
        {nodes.length > 0 && (
          <div className={styles.treeBox}>
            <Tree
              value={nodes}
              expandedKeys={expandedKeys}
              onToggle={e => setExpandedKeys(e.value as Record<string, boolean>)}
              nodeTemplate={nodeTemplate}
              style={{ fontSize: '0.85rem' }}
            />
          </div>
        )}
      </div>

      {/* Sidebar (right) */}
      <div className={styles.sidebar}>
        <div className={styles.subsectionTitle} style={{ fontSize: '0.85rem' }}>Library</div>
        <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Drag into the form tree →</div>

        {/* Unreferenced data fields */}
        <div style={{ fontWeight: 600, fontSize: '0.75rem', color: '#475569', margin: '0.4rem 0 0.2rem' }}>
          Unreferenced data fields ({unreferencedDataFields.length})
        </div>
        {unreferencedDataFields.length === 0 && <div style={{ fontSize: '0.72rem', color: '#aaa' }}>— none —</div>}
        {unreferencedDataFields.map(f => (
          <div key={f.slug}
            draggable
            onDragStart={e => handleDragStart(e, 'datafield', f.slug)}
            onDragEnd={() => { setDragKind(null); setDragOverKey(null) }}
            style={{ padding: '0.3rem 0.4rem', margin: '0.2rem 0', background: '#fff', border: '1px solid #e2e8f0', borderRadius: '4px', cursor: 'grab', fontSize: '0.8rem' }}>
            <span style={{ fontFamily: 'monospace' }}>{f.slug}</span>
            <span style={{ color: '#888', fontSize: '0.68rem', marginLeft: '0.3rem' }}>{f.data_type}</span>
          </div>
        ))}

        {/* Referenced data fields */}
        <div style={{ fontWeight: 600, fontSize: '0.75rem', color: '#475569', margin: '0.6rem 0 0.2rem' }}>
          Referenced data fields ({referencedDataFields.length})
        </div>
        {referencedDataFields.length === 0 && <div style={{ fontSize: '0.72rem', color: '#aaa' }}>— none —</div>}
        {referencedDataFields.map(f => (
          <div key={f.slug}
            draggable
            onDragStart={e => handleDragStart(e, 'datafield', f.slug)}
            onDragEnd={() => { setDragKind(null); setDragOverKey(null) }}
            style={{ padding: '0.3rem 0.4rem', margin: '0.2rem 0', background: '#fff', border: '1px solid #e2e8f0', borderRadius: '4px', cursor: 'grab', fontSize: '0.8rem', opacity: 0.7 }}>
            <span style={{ fontFamily: 'monospace' }}>{f.slug}</span>
            <span style={{ color: '#888', fontSize: '0.68rem', marginLeft: '0.3rem' }}>{f.data_type}</span>
          </div>
        ))}

        {/* Unconfigured widget types */}
        <div style={{ fontWeight: 600, fontSize: '0.75rem', color: '#475569', margin: '0.8rem 0 0.2rem' }}>
          Widgets & layout
        </div>
        {[...WIDGET_ELEMENT_TYPES, ...STRUCTURAL_ELEMENT_TYPES].map(t => (
          <div key={t}
            draggable
            onDragStart={e => handleDragStart(e, 'widget', t)}
            onDragEnd={() => { setDragKind(null); setDragOverKey(null) }}
            style={{ padding: '0.3rem 0.4rem', margin: '0.2rem 0', background: '#fff', border: '1px solid #e2e8f0', borderRadius: '4px', cursor: 'grab', fontSize: '0.8rem' }}>
            <span style={{ fontSize: '0.68rem', fontWeight: 600, color: '#6b7280' }}>{t}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Inline FormElement editor (labels, bindings, type_config) ──────────────────

interface FormElementEditorProps {
  el: FormElementIn
  dataFields: FieldDefinitionIn[]
  languages: string[]
  onChange: (el: FormElementIn) => void
}

function FormElementEditor({ el, dataFields, languages, onChange }: FormElementEditorProps) {
  const isStructural = (STRUCTURAL_ELEMENT_TYPES as readonly string[]).includes(el.element_type)
  const isWidget = el.element_type === 'field' || el.element_type === 'date_range'

  function setBindings(bindings: FormElementIn['bindings']) {
    onChange({ ...el, bindings: bindings ?? [] })
  }

  // Roles are preset by element type (no freetext). Normalize the bindings to
  // exactly the expected roles, preserving any existing data_field_slug for a
  // matching role. Extra/unknown roles are dropped.
  const roles = BINDING_ROLES[el.element_type] ?? []
  const existingByRole = new Map((el.bindings ?? []).map(b => [b.role, b.data_field_slug]))
  const presetBindings = roles.map(r => ({
    data_field_slug: existingByRole.get(r) ?? '',
    role: r,
  }))
  function setBindingForRole(role: string, dataFieldSlug: string) {
    setBindings(presetBindings.map(b => (b.role === role ? { ...b, data_field_slug: dataFieldSlug } : b)))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <div className={styles.formGroup}>
        <label className={styles.label}>Slug</label>
        <input className={styles.input} value={el.slug}
          onChange={e => onChange({ ...el, slug: e.target.value })} />
      </div>

      {isWidget && (
        <div>
          <label className={styles.label} style={{ margin: '0.3rem 0' }}>Bindings (data fields)</label>
          {presetBindings.map(b => (
            <div key={b.role} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.3rem' }}>
              <span style={{ flex: '0 0 4rem', fontSize: '0.78rem', fontWeight: 600, color: '#475569' }}>
                {b.role === '' ? 'field' : b.role}
              </span>
              <select className={styles.select} value={b.data_field_slug}
                onChange={e => setBindingForRole(b.role, e.target.value)} style={{ flex: 1 }}>
                <option value="">— select data field —</option>
                {dataFields.map(f => <option key={f.slug} value={f.slug}>{f.slug} ({f.data_type})</option>)}
              </select>
            </div>
          ))}
          {el.element_type === 'date_range' && (
            <div style={{ fontSize: '0.7rem', color: '#888' }}>Bind two <code>date</code> fields as <b>from</b> and <b>to</b>.</div>
          )}
        </div>
      )}

      {!isStructural && (
        <div>
          <div style={{ fontWeight: 600, fontSize: '0.78rem', margin: '0.3rem 0' }}>Labels</div>
          {languages.map(lang => (
            <div key={lang} className={styles.formGroup}>
              <label className={styles.label}>{lang}</label>
              <input className={styles.input} value={(el.labels ?? {})[lang] ?? ''}
                onChange={e => onChange({ ...el, labels: { ...(el.labels ?? {}), [lang]: e.target.value } })} />
            </div>
          ))}
        </div>
      )}

      {!isStructural && (
        <div>
          <div style={{ fontWeight: 600, fontSize: '0.78rem', margin: '0.3rem 0' }}>Help Text</div>
          {languages.map(lang => (
            <div key={lang} className={styles.formGroup}>
              <label className={styles.label}>{lang}</label>
              <input className={styles.input} value={(el.help_texts ?? {})[lang] ?? ''}
                placeholder={`Help text (${lang})`}
                onChange={e => onChange({ ...el, help_texts: { ...(el.help_texts ?? {}), [lang]: e.target.value } })} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
