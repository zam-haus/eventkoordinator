import { useCallback, useEffect, useState } from 'react'
import { udmGetTypeConfig, udmListTypeEditorTabs, type TypeEditorTabOut } from '../apiUdm'
import { typeEditorTabRegistry } from './registry'
import { JsonTabFallback } from './JsonTabFallback'
import styles from '../UdmAdminPage.module.css'

interface TypeEditorTabsPanelProps {
  typeId: string
}

/** events-and-sync.md §5/Step 11: renders every registered plugin
 *  type-editor tab (`GET /type-editor-tabs/`) for the type's current
 *  config version, using the registered component for a tab id if one
 *  exists (`typeEditorTabRegistry`), otherwise the generic JSON editor
 *  fallback. Lives in `TypeDetail` (UdmAdminPage.tsx) alongside the
 *  Field Config / Policies sections, in the same section-per-concern
 *  style used there. */
export function TypeEditorTabsPanel({ typeId }: TypeEditorTabsPanelProps) {
  const [tabs, setTabs] = useState<TypeEditorTabOut[]>([])
  const [configVersionId, setConfigVersionId] = useState<string | null>(null)
  const [activeTabId, setActiveTabId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [tabList, version] = await Promise.all([udmListTypeEditorTabs(), udmGetTypeConfig(typeId)])
      setTabs(tabList)
      setConfigVersionId(version.version_id)
      setActiveTabId(prev => prev ?? tabList[0]?.id ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load type editor tabs')
    } finally {
      setLoading(false)
    }
  }, [typeId])

  useEffect(() => { void load() }, [load])

  if (loading) return <div className={styles.emptyState}>Loading…</div>
  if (error) return <div className={styles.error}>{error}</div>
  if (tabs.length === 0) return null
  if (!configVersionId) {
    return <div className={styles.error}>No config version for this type — assign a field config first.</div>
  }

  const ActiveComponent = activeTabId
    ? (typeEditorTabRegistry[activeTabId] ?? JsonTabFallback)
    : null

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>Plugin Tabs</div>
      <div className={styles.row} style={{ marginBottom: '0.75rem', gap: '0.4rem' }}>
        {tabs.map(t => (
          <button
            key={t.id}
            type="button"
            className={`${styles.btn} ${activeTabId === t.id ? styles.btnPrimary : styles.btnSecondary}`}
            onClick={() => setActiveTabId(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {ActiveComponent && activeTabId && (
        <ActiveComponent key={activeTabId} tabId={activeTabId} configVersionId={configVersionId} />
      )}
    </div>
  )
}
