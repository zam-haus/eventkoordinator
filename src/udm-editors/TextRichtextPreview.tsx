import DOMPurify from 'dompurify'
import type { PreviewProps } from './FieldPreview'
import { fieldPreviewRegistry } from './registry-preview'

function TextRichtextPreview({ value }: PreviewProps) {
  const html = (value as string) ?? ''
  if (!html) return <span style={{ color: '#9ca3af' }}>—</span>
  // The text_richtext editor is a plain textarea, so this value is user-supplied
  // HTML. Sanitize with DOMPurify before injecting it to prevent stored XSS.
  return (
    <div
      style={{ fontSize: '0.9rem', color: '#374151', lineHeight: 1.5 }}
      dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }}
    />
  )
}

fieldPreviewRegistry.register('text_richtext', TextRichtextPreview)
