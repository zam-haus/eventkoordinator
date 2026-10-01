import type { TFunction } from 'i18next'

/**
 * Ask for confirmation before an action that sends mail to the proposal owner
 * or to a reviewer, describing who gets mailed and what the mail says.
 *
 * Returns true when the action may proceed, i.e. when there is no mail to warn
 * about or the user confirmed the dialog.
 */
export function confirmMailAction(
  t: TFunction,
  warningId: string | null | undefined,
  vars?: Record<string, unknown>,
): boolean {
  if (!warningId) return true
  const message = t(`mails.confirm.${warningId}`, {
    ...vars,
    defaultValue: t('mails.confirm.generic'),
  })
  return window.confirm(message)
}
