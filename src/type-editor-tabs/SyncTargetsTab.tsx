import { useEffect, useState } from 'react'
import { udmGetTypeEditorTabConfig, udmPutTypeEditorTabConfig, UdmApiError } from '../apiUdm'
import type { TypeEditorTabProps } from './types'
import styles from '../UdmAdminPage.module.css'

interface SyncTargetsTabConfig {
  target_keys: string[]
}

/** Dedicated editor for sync_core's "sync_targets" tab (events-and-sync.md
 *  §5/§8, Step 11): which SyncBaseTarget keys this type may mark_sync
 *  against. There is no API listing existing SyncBaseTarget rows from the
 *  UDM surface (targets are managed in Django admin), so this is a plain
 *  editable list of key strings rather than a picker — still far better
 *  than the raw-JSON fallback for the common case of adding/removing one
 *  key. */
export function SyncTargetsTab({ tabId, configVersionId }: TypeEditorTabProps) {
  const [keys, setKeys] = useState<string[]>([])
  const [newKey, setNewKey] = useState('')
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
        const config = res.config as Partial<SyncTargetsTabConfig>
        setKeys(Array.isArray(config.target_keys) ? config.target_keys : [])
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load tab config') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tabId, configVersionId])

  async function save(nextKeys: string[]) {
    setError(null)
    setSuccess(null)
    setSaving(true)
    try {
      const res = await udmPutTypeEditorTabConfig(configVersionId, tabId, { target_keys: nextKeys })
      const config = res.config as Partial<SyncTargetsTabConfig>
      setKeys(Array.isArray(config.target_keys) ? config.target_keys : nextKeys)
      setSuccess('Saved.')
    } catch (e) {
      setError(e instanceof UdmApiError ? e.allMessages.join('; ') : (e instanceof Error ? e.message : 'Save failed'))
    } finally {
      setSaving(false)
    }
  }

  function handleAdd() {
    const key = newKey.trim()
    if (!key || keys.includes(key)) return
    setNewKey('')
    void save([...keys, key])
  }

  function handleRemove(key: string) {
    void save(keys.filter(k => k !== key))
  }

  if (loading) return <div className={styles.emptyState}>Loading…</div>

  return (
    <div>
      <p style={{ color: '#888', fontSize: '0.85rem', marginTop: 0 }}>
        SyncBaseTarget keys this type's entities may be marked pending against via mark_sync.
        Targets themselves (URLs, credentials) are managed in Django admin.
      </p>
      {keys.length === 0 && <div className={styles.emptyState}>No targets bound yet.</div>}
      {keys.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 0.75rem' }}>
          {keys.map(key => (
            <li key={key} className={styles.row} style={{ justifyContent: 'space-between', padding: '0.25rem 0' }}>
              <code>{key}</code>
              <button type="button" className={styles.btn} disabled={saving} onClick={() => handleRemove(key)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className={styles.row}>
        <input
          type="text"
          value={newKey}
          onChange={e => setNewKey(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAdd() } }}
          placeholder="e.g. webhook:main"
        />
        <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} disabled={saving || !newKey.trim()} onClick={handleAdd}>
          Add
        </button>
      </div>
      {error && <div className={styles.error}>{error}</div>}
      {success && <div className={styles.success}>{success}</div>}
    </div>
  )
}
