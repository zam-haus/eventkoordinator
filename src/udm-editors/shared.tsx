import { Tooltip } from 'primereact/tooltip'
import type { PolicyMessage } from './types'

const MSG_COLORS: Record<string, { text: string; bg: string; icon?: string }> = {
  critical: { text: '#991b1b', bg: '#fef2f2' },
  error:    { text: '#991b1b', bg: '#fef2f2' },
  warning:  { text: '#92400e', bg: '#fffbeb' },
  info:     { text: '#1e40af', bg: '#eff6ff' },
  success:  { text: '#15803d', bg: '#f0fdf4', icon: '✓' },
}

const SEVERITY_ICON: Record<string, string> = {
  critical: 'pi-times-circle',
  error:    'pi-times-circle',
  warning:  'pi-exclamation-circle',
  info:     'pi-info-circle',
  success:  'pi-check-circle',
}

/** Small icon that shows a tooltip with `messages` on hover — the compact
 *  alternative to `PolicyMessageList` for tight spaces like table cells. */
export function SeverityIndicator({ severity, messages, targetKey }: {
  severity: string
  messages: PolicyMessage[]
  targetKey: string
}) {
  const iconClass = SEVERITY_ICON[severity] ?? 'pi-info-circle'
  const color = MSG_COLORS[severity] ?? MSG_COLORS.info
  const targetId = `udm-sev-${targetKey.replace(/[^a-zA-Z0-9]/g, '-')}`
  return (
    <>
      <Tooltip target={`#${targetId}`} position="right">
        <ul style={{ margin: 0, padding: '0 0 0 1rem', fontSize: '0.8rem', maxWidth: '260px' }}>
          {messages.map((m, i) => <li key={i}>{m.text}</li>)}
        </ul>
      </Tooltip>
      <i id={targetId} className={`pi ${iconClass}`} style={{ color: color.text, cursor: 'help', marginLeft: '0.3rem' }} />
    </>
  )
}

export function PolicyMessageList({ messages }: { messages: PolicyMessage[] }) {
  return (
    <ul style={{ margin: '0.4rem 0 0', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
      {messages.map((m, i) => {
        const color = MSG_COLORS[m.level] ?? MSG_COLORS.info
        return (
          <li key={i} style={{
            fontSize: '0.8rem', padding: '0.2rem 0.5rem',
            borderRadius: '4px', color: color.text, background: color.bg,
            display: 'flex', alignItems: 'center', gap: '0.3rem',
          }}>
            {color.icon && <span style={{ fontWeight: 700, flexShrink: 0 }}>{color.icon}</span>}
            {m.text}
          </li>
        )
      })}
    </ul>
  )
}

/** The field's slug, shown next to its label — it is what filter queries and
 *  policies address the field by, and it is not derivable from the label. */
export function FieldSlug({ slug }: { slug: string }) {
  return (
    <code style={{
      fontFamily: 'monospace', fontSize: '0.72rem', fontWeight: 400,
      color: '#6b7280', marginLeft: '0.35rem',
    }}>
      {slug}
    </code>
  )
}

/** Small badge shown next to a field label when the policy leaves it read-only. */
export function ReadonlyBadge() {
  return (
    <span style={{
      fontSize: '0.65rem', fontWeight: 600, letterSpacing: '0.03em',
      textTransform: 'uppercase', color: '#64748b', background: '#f1f5f9',
      border: '1px solid #cbd5e1', borderRadius: '999px',
      padding: '0.05rem 0.45rem', marginLeft: '0.4rem', verticalAlign: 'middle',
      whiteSpace: 'nowrap',
    }}>
      read-only
    </span>
  )
}
