import { useEffect, useRef, type ReactNode } from 'react'
import { Button } from 'primereact/button'
import styles from '../UdmEntityEditor.module.css'

/** Field types whose editor is too tall for an inline input group; buttons go below. */
export const LARGE_TYPES = new Set(['text_long', 'text_markdown', 'text_richtext'])

/**
 * Field types where leaving the field auto-commits. Focus moving into a
 * portaled overlay panel (dropdown/multiselect/calendar) does not count as
 * leaving — see OVERLAY_SELECTOR. File uploads save via the check button only.
 */
export const BLUR_COMMIT_TYPES = new Set([
  'text_short', 'text_long', 'text_richtext', 'text_markdown',
  'integer', 'float', 'date', 'time', 'datetime', 'boolean',
  'select_single', 'select_multi',
  'user_select', 'user_select_multi',
  'group_select', 'group_select_multi',
  'entity_select', 'entity_select_multi',
])

const OVERLAY_SELECTOR = [
  '.p-dropdown-panel', '.p-multiselect-panel', '.p-datepicker',
  '.p-autocomplete-panel', '.p-connected-overlay-enter-done',
].join(', ')

export interface FieldCommitWrapperProps {
  dirty: boolean
  saving: boolean
  large: boolean
  blurCommit: boolean
  disabled?: boolean
  onCommit: () => void
  onCancel: () => void
  children: ReactNode
}

/**
 * Wraps a field editor with InputGroup-style check/cancel buttons and
 * commits the single field on focus leaving the wrapper (when enabled).
 */
export function FieldCommitWrapper({
  dirty, saving, large, blurCommit, disabled, onCommit, onCancel, children,
}: FieldCommitWrapperProps) {
  const ref = useRef<HTMLDivElement>(null)
  const stateRef = useRef({ dirty, saving, blurCommit, disabled, onCommit })
  useEffect(() => { stateRef.current = { dirty, saving, blurCommit, disabled, onCommit } })

  function handleBlur() {
    // Defer so document.activeElement reflects where focus actually went.
    setTimeout(() => {
      const s = stateRef.current
      if (!s.dirty || s.saving || !s.blurCommit || s.disabled) return
      const active = document.activeElement
      if (active && ref.current?.contains(active)) return
      if (active?.closest(OVERLAY_SELECTOR)) return
      s.onCommit()
    }, 0)
  }

  const showButtons = dirty && !disabled
  const buttons = showButtons ? (
    <>
      <Button type="button" className="commitBtn commitBtnCheck" icon="pi pi-check" severity="success"
        size="small" loading={saving} onClick={onCommit} aria-label="Save field" />
      <Button type="button" className="commitBtn commitBtnCancel" icon="pi pi-times" severity="danger"
        size="small" disabled={saving}
        onMouseDown={e => e.preventDefault()}
        onClick={onCancel} aria-label="Discard change" />
    </>
  ) : null

  if (large) {
    return (
      <div ref={ref} onBlur={handleBlur}>
        {children}
        {buttons && <div className={styles.commitButtons}>{buttons}</div>}
      </div>
    )
  }
  return (
    <div ref={ref} onBlur={handleBlur}
      className={`p-inputgroup${showButtons ? ` ${styles.commitGroupDirty}` : ''}`}>
      <div className={styles.commitGroupField}>{children}</div>
      {buttons}
    </div>
  )
}
