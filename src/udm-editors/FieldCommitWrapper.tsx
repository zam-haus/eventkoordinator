import { useEffect, useRef, type ReactNode } from 'react'
import { Button } from 'primereact/button'
import styles from '../UdmEntityEditor.module.css'

/** Field types whose editor is too tall for an inline input group; buttons go below. */
export const LARGE_TYPES = new Set(['text_long', 'text_markdown', 'text_richtext', 'image', 'file'])

/**
 * Field types where leaving the field auto-commits. Focus moving into a
 * portaled overlay panel (dropdown/multiselect/calendar) does not count as
 * leaving — see OVERLAY_SELECTOR.
 */
export const BLUR_COMMIT_TYPES = new Set([
  'text_short', 'text_long', 'text_richtext', 'text_markdown',
  'integer', 'float', 'date', 'time', 'datetime', 'boolean',
  'select_single', 'select_multi',
  'user_select', 'user_select_multi',
  'group_select', 'group_select_multi',
  'entity_select', 'entity_select_multi',
  'image', 'file',
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
 * commits the single field when the user leaves it (when enabled). "Leaving"
 * is detected two ways, because either alone misses cases:
 * - focus blur out of the wrapper (keyboard tabbing, focusable widgets)
 * - pointerdown outside the wrapper (clear icons and buttons that unmount on
 *   click, or editors where focus never entered the wrapper at all)
 */
export function FieldCommitWrapper({
  dirty, saving, large, blurCommit, disabled, onCommit, onCancel, children,
}: FieldCommitWrapperProps) {
  const ref = useRef<HTMLDivElement>(null)
  const stateRef = useRef({ dirty, saving, blurCommit, disabled, onCommit })
  useEffect(() => { stateRef.current = { dirty, saving, blurCommit, disabled, onCommit } })
  // Prevents blur + outside-click double-firing before the saving prop updates
  const firedRef = useRef(false)
  useEffect(() => { if (!dirty || saving) firedRef.current = false }, [dirty, saving])

  function tryCommit() {
    const s = stateRef.current
    if (!s.dirty || s.saving || !s.blurCommit || s.disabled || firedRef.current) return
    firedRef.current = true
    s.onCommit()
  }

  function handleBlur() {
    // Defer so document.activeElement reflects where focus actually went.
    setTimeout(() => {
      const active = document.activeElement
      if (active && ref.current?.contains(active)) return
      if (active?.closest(OVERLAY_SELECTOR)) return
      tryCommit()
    }, 0)
  }

  // Clicks outside the field commit too — needed for controls that never
  // receive focus or unmount on click (e.g. a dropdown's clear icon).
  useEffect(() => {
    if (!dirty || !blurCommit || disabled) return
    function onPointerDown(ev: Event) {
      const t = ev.target as Element | null
      if (!t || !(t instanceof Element)) return
      if (ref.current?.contains(t)) return
      if (t.closest(OVERLAY_SELECTOR)) return
      tryCommit()
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    return () => document.removeEventListener('pointerdown', onPointerDown, true)
  }, [dirty, blurCommit, disabled])

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
