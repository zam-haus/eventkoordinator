import type { PolicyMessage } from './types'

const MSG_COLORS: Record<string, { text: string; bg: string; icon?: string }> = {
  critical: { text: '#991b1b', bg: '#fef2f2' },
  error:    { text: '#991b1b', bg: '#fef2f2' },
  warning:  { text: '#92400e', bg: '#fffbeb' },
  info:     { text: '#1e40af', bg: '#eff6ff' },
  success:  { text: '#15803d', bg: '#f0fdf4', icon: '✓' },
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
