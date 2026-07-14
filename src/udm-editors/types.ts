import { useState, useRef, useCallback } from 'react'
import type { FieldDefinitionOut, PolicyMessage } from '../apiUdm'

export type { FieldDefinitionOut, PolicyMessage }

export interface FieldInputProps {
  fd: FieldDefinitionOut
  value: unknown
  onChange: (v: unknown) => void
  disabled: boolean
  lang?: string
  entityChildren?: Record<string, unknown[]>
  subFieldSeverities?: Record<string, string>
  subFieldMessages?: Record<string, PolicyMessage[]>
  resetKey?: number
  nodeId?: string | null
  onEntityRefresh?: (policyMessages?: PolicyMessage[]) => void | Promise<void>
  compact?: boolean
  /** Immediately persist submodel ops (create/update/delete) for this field.
   *  Throws on failure. When absent, submodel editors stage ops locally (legacy). */
  onCommitOps?: (ops: unknown) => Promise<void>
}

export function getLang(map: Record<string, string>, uiLang: string): string {
  return map[uiLang] ?? map['en'] ?? Object.values(map)[0] ?? ''
}

export function useAutocomplete<T>(fetcher: (q: string) => Promise<T[]>, delay = 300) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<T[]>([])
  const [open, setOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const search = useCallback((q: string) => {
    setQuery(q)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      if (q.length < 1) { setResults([]); setOpen(false); return }
      const res = await fetcher(q)
      setResults(res)
      setOpen(true)
    }, delay)
  }, [fetcher, delay])

  return { query, setQuery, results, setResults, open, setOpen, search }
}
