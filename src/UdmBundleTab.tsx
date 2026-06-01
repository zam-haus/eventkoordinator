import { useState, useEffect, useCallback, useRef } from 'react'
import {
  udmListTypes,
  udmExportBundleZip,
  udmParseBundleZip,
  udmImportBundleZip,
  type UDMTypeOut,
} from './apiUdm'
import styles from './UdmAdminPage.module.css'

// ── Export panel ──────────────────────────────────────────────────────────────

function ExportPanel({ types }: { types: UDMTypeOut[] }) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function toggleType(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function handleExport() {
    if (selectedIds.size === 0) { setError('Select at least one UDM Type'); return }
    setError(null)
    setExporting(true)
    try {
      const blob = await udmExportBundleZip([...selectedIds])
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const names = types
        .filter(t => selectedIds.has(t.id))
        .map(t => t.name.toLowerCase().replace(/\s+/g, '_'))
        .join('_')
      a.download = `udm_bundle_${names}.zip`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>Export Bundle</div>
      <p style={{ fontSize: '0.875rem', color: '#555', marginBottom: '0.75rem' }}>
        Select one or more UDM Types to export. The downloaded <code>.zip</code> file
        contains <code>UDM_BUNDLE.json</code> (structural config) and a <code>policies/</code> directory
        with each policy as a separate <code>.rego</code> file.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginBottom: '1rem' }}>
        {types.length === 0 && <div className={styles.emptyState}>No UDM Types found.</div>}
        {types.map(t => (
          <label key={t.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.9rem' }}>
            <input
              type="checkbox"
              checked={selectedIds.has(t.id)}
              onChange={() => toggleType(t.id)}
              style={{ width: '1rem', height: '1rem', cursor: 'pointer' }}
            />
            <span style={{ fontWeight: 500 }}>{t.name}</span>
            {t.description && <span style={{ color: '#777', fontSize: '0.8rem' }}>— {t.description}</span>}
          </label>
        ))}
      </div>

      {error && <div className={styles.error} style={{ marginBottom: '0.5rem' }}>{error}</div>}
      <button
        type="button"
        className={`${styles.btn} ${styles.btnPrimary}`}
        onClick={() => void handleExport()}
        disabled={exporting || selectedIds.size === 0}
      >
        {exporting ? 'Exporting…' : `Export ZIP${selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}`}
      </button>
    </div>
  )
}

// ── Import panel ──────────────────────────────────────────────────────────────

interface BundleTypeInfo { id: string; name: string; description: string }

function ImportPanel({ types }: { types: UDMTypeOut[] }) {
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // Types declared in the bundle (may not exist locally — will be created on import)
  const [bundleTypes, setBundleTypes] = useState<BundleTypeInfo[]>([])
  const [importing, setImporting] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ imported_workflows: number; imported_configs: number; imported_policies: number } | null>(null)
  const parseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const localTypeIds = new Set(types.map(t => t.id))

  function toggleType(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null
    setZipFile(file)
    setResult(null)
    setError(null)
    setBundleTypes([])
    setSelectedIds(new Set())
  }

  // Auto-detect scope type IDs from ZIP via server parse
  useEffect(() => {
    if (!zipFile) return
    if (parseTimerRef.current) clearTimeout(parseTimerRef.current)
    setParsing(true)
    parseTimerRef.current = setTimeout(async () => {
      const res = await udmParseBundleZip(zipFile)
      setParsing(false)
      setBundleTypes(res.udm_types ?? [])
      // Pre-select all bundle types (they exist locally or will be created)
      if (res.scope_type_ids.length > 0) setSelectedIds(new Set(res.scope_type_ids))
    }, 300)
    return () => { if (parseTimerRef.current) clearTimeout(parseTimerRef.current) }
  }, [zipFile, types])

  async function handleImport() {
    if (!zipFile) { setError('Load a ZIP bundle file first'); return }
    if (selectedIds.size === 0) { setError('Select the scope UDM Types to import into'); return }
    setError(null)
    setResult(null)
    setImporting(true)
    try {
      const res = await udmImportBundleZip(zipFile, [...selectedIds])
      setResult(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>Import Bundle</div>
      <p style={{ fontSize: '0.875rem', color: '#555', marginBottom: '0.75rem' }}>
        Load a <code>.zip</code> bundle file (exported above). Configs and workflows exclusively
        used by the selected scope types will be updated in place; shared ones will be cloned so
        out-of-scope types are untouched. Policy <code>.rego</code> files in the ZIP are saved
        to the database by their slug.
      </p>

      <div className={styles.formGroup} style={{ marginBottom: '0.75rem' }}>
        <label className={styles.label}>Bundle ZIP file</label>
        <input
          type="file"
          accept=".zip"
          onChange={handleFileChange}
          style={{ fontSize: '0.875rem' }}
        />
        {zipFile && (
          <div style={{ fontSize: '0.8rem', color: '#555', marginTop: '0.25rem' }}>
            {parsing ? '⟳ Detecting scope types…' : `Loaded: ${zipFile.name}`}
          </div>
        )}
      </div>

      <div className={styles.formGroup} style={{ marginBottom: '1rem' }}>
        <label className={styles.label}>Scope UDM Types</label>
        <div style={{ fontSize: '0.8rem', color: '#666', marginBottom: '0.35rem' }}>
          Which types does this bundle apply to? Auto-detected from the ZIP — bundle types will be
          created if they don't exist. Local types can be added as additional targets.
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
          {/* Bundle types (may not exist locally — shown first) */}
          {bundleTypes.map(bt => (
            <label key={bt.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.875rem' }}>
              <input
                type="checkbox"
                checked={selectedIds.has(bt.id)}
                onChange={() => toggleType(bt.id)}
                style={{ width: '1rem', height: '1rem', cursor: 'pointer' }}
              />
              <span style={{ fontWeight: 500 }}>{bt.name}</span>
              {!localTypeIds.has(bt.id) && (
                <span style={{ fontSize: '0.75rem', color: '#0077cc', fontStyle: 'italic' }}>will be created</span>
              )}
              <span className={styles.monoText} style={{ fontSize: '0.75rem', color: '#aaa' }}>{bt.id}</span>
            </label>
          ))}
          {/* Local types not in bundle (can be used as fallback targets) */}
          {types.filter(t => !bundleTypes.some(bt => bt.id === t.id)).map(t => (
            <label key={t.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.875rem' }}>
              <input
                type="checkbox"
                checked={selectedIds.has(t.id)}
                onChange={() => toggleType(t.id)}
                style={{ width: '1rem', height: '1rem', cursor: 'pointer' }}
              />
              <span style={{ fontWeight: 500 }}>{t.name}</span>
              <span style={{ fontSize: '0.75rem', color: '#888', fontStyle: 'italic' }}>link as target</span>
              <span className={styles.monoText} style={{ fontSize: '0.75rem', color: '#aaa' }}>{t.id}</span>
            </label>
          ))}
          {types.length === 0 && bundleTypes.length === 0 && (
            <div className={styles.emptyState}>Load a ZIP file to see types.</div>
          )}
        </div>
      </div>

      {error && <div className={styles.error} style={{ marginBottom: '0.5rem' }}>{error}</div>}
      {result && (
        <div className={styles.success} style={{ marginBottom: '0.5rem' }}>
          Import complete — {result.imported_workflows} workflow{result.imported_workflows !== 1 ? 's' : ''},
          {' '}{result.imported_configs} field config{result.imported_configs !== 1 ? 's' : ''},
          {' '}{result.imported_policies} polic{result.imported_policies !== 1 ? 'ies' : 'y'} processed.
        </div>
      )}

      <button
        type="button"
        className={`${styles.btn} ${styles.btnPrimary}`}
        onClick={() => void handleImport()}
        disabled={importing || !zipFile}
      >
        {importing ? 'Importing…' : 'Import Bundle'}
      </button>
    </div>
  )
}

// ── Main export ───────────────────────────────────────────────────────────────

export function BundleTab() {
  const [types, setTypes] = useState<UDMTypeOut[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try { setTypes(await udmListTypes()) } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => { void load() }, [load])

  if (loading) return <div className={styles.emptyState}>Loading…</div>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <ExportPanel types={types} />
      <ImportPanel types={types} />
    </div>
  )
}
