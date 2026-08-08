import type { PreviewProps } from './FieldPreview'
import { fieldPreviewRegistry } from './registry-preview'

function FilePreview({ fd, value }: PreviewProps) {
  if (value == null) return <span style={{ color: '#9ca3af' }}>—</span>
  const f = value as Record<string, string>
  const url = f['url'] ?? null
  const name = f['original_name'] ?? 'File'
  if (!url) return <span style={{ color: '#9ca3af' }}>—</span>

  if (fd.data_type === 'image') {
    return (
      <img src={url} alt={name} style={{ maxHeight: 80, maxWidth: 160, borderRadius: 4, display: 'block', border: '1px solid #e5e7eb' }} />
    )
  }

  return (
    <a href={url} target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.88rem', color: '#2563eb', textDecoration: 'underline' }}>
      📎 {name}
    </a>
  )
}

fieldPreviewRegistry.register(['image', 'file'], FilePreview)
