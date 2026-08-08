import type { PreviewProps } from './FieldPreview'
import { fieldPreviewRegistry } from './registry-preview'

function NumberPreview({ fd, value }: PreviewProps) {
  if (value == null) return <span style={{ color: '#9ca3af' }}>—</span>
  // Decimal/float values can arrive as scientific-notation strings (e.g.
  // "0E-10") rather than numbers — coerce before formatting, otherwise
  // toLocaleString() on a string is a no-op and the raw notation leaks through.
  const num = Number(value)
  let formatted: string
  if (fd.data_type === 'integer') {
    formatted = num.toLocaleString(undefined, { maximumFractionDigits: 0 })
  } else {
    // Respect the field's configured decimal_places (e.g. "5.00", not "5")
    // instead of letting toLocaleString strip trailing zeros.
    const decimalPlaces = (fd.type_config as Record<string, unknown> | undefined)?.['decimal_places']
    const digits = typeof decimalPlaces === 'number' ? decimalPlaces : undefined
    formatted = num.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits ?? 6,
    })
  }
  return <span style={{ fontSize: '0.9rem', color: '#374151', fontVariantNumeric: 'tabular-nums' }}>{formatted}</span>
}

fieldPreviewRegistry.register(['integer', 'float'], NumberPreview)
