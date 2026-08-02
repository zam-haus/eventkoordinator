import { useState, useRef, useEffect } from 'react'
import { udmUploadStagingFile } from '../apiUdm'
import type { FieldInputProps } from './types'
import { fieldEditorRegistry } from './registry'
import styles from '../UdmEntityEditor.module.css'

function FileEditor({ fd, value, onChange, disabled }: FieldInputProps) {
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [stagingName, setStagingName] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const uploadAreaRef = useRef<HTMLDivElement>(null)

  async function handleFile(file: File) {
    setUploading(true)
    setProgress(0)
    try {
      const staging = await udmUploadStagingFile(file, fd.id, setProgress)
      onChange({ staging_id: staging.staging_id })
      setStagingName(file.name)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const currentFile = value && typeof value === 'object' ? value as Record<string, string> : null
  const currentUrl = currentFile?.['url'] ?? null

  useEffect(() => {
    if (currentUrl) setStagingName(null)
  }, [currentUrl])
  const isImage = fd.data_type === 'image'
  const hasValue = stagingName !== null || currentUrl !== null

  function handleClear() {
    // The Clear button unmounts after clearing (no value left), and removing
    // a focused element fires no blur event — so park focus on the upload
    // area, which persists, to keep the commit wrapper's blur-save armed.
    uploadAreaRef.current?.focus()
    setStagingName(null)
    onChange(null)
  }

  return (
    <div>
      {currentUrl && !stagingName && (
        <div className={styles.fileInfo}>
          {isImage ? (
            <img src={currentUrl} alt={currentFile?.['original_name'] ?? 'image'}
              style={{ maxWidth: '100%', maxHeight: '200px', display: 'block', borderRadius: '4px', marginBottom: '0.4rem' }} />
          ) : (
            <a href={currentUrl} target="_blank" rel="noopener noreferrer">
              {currentFile?.['original_name'] ?? 'View file'}
            </a>
          )}
        </div>
      )}
      {stagingName && (
        <div className={styles.fileInfo}>Staged: {stagingName} — saved when you leave the field</div>
      )}
      {!disabled && (
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.4rem' }}>
          <div
            ref={uploadAreaRef}
            className={styles.fileUploadArea}
            style={{ flex: 1, marginTop: 0 }}
            // Focusable so the commit wrapper's blur-save fires when the user
            // clicks elsewhere after staging an upload
            tabIndex={disabled ? undefined : 0}
            role="button"
            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click() } }}
            onClick={() => inputRef.current?.click()}
            onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) void handleFile(f) }}
            onDragOver={e => e.preventDefault()}
          >
            {uploading ? `Uploading… ${progress}%` : 'Click or drop to upload'}
          </div>
          {hasValue && (
            <button
              type="button"
              onClick={handleClear}
              style={{ padding: '0.4rem 0.75rem', border: '1px solid #dc2626', borderRadius: '4px', background: '#fff', color: '#dc2626', cursor: 'pointer', fontSize: '0.82rem', whiteSpace: 'nowrap' }}
            >
              Clear
            </button>
          )}
          <input
            ref={inputRef}
            type="file"
            style={{ display: 'none' }}
            accept={fd.data_type === 'image' ? 'image/*' : undefined}
            onChange={e => { const f = e.target.files?.[0]; if (f) void handleFile(f) }}
          />
        </div>
      )}
    </div>
  )
}

fieldEditorRegistry.register(['image', 'file'], FileEditor)
