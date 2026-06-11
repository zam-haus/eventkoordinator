import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog } from 'primereact/dialog'
import { onSessionExpired, onReauthSuccess } from './sessionExpiry'
import { getSsoAuthenticateUrl, submitOidcLogout } from './oidcAuth'
import styles from './SessionExpiredDialog.module.css'

// Global popup shown when an API call detects that the authentication session
// has expired (the backend redirected the request instead of answering it).
export function SessionExpiredDialog() {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(false)
  const [reauthPending, setReauthPending] = useState(false)

  useEffect(() => onSessionExpired(() => {
    setReauthPending(false)
    setVisible(true)
  }), [])

  // The re-auth success page (new tab) broadcasts once the user is logged in
  // again; dismiss the dialog so the original tab can continue. The next request
  // transparently uses the refreshed session/CSRF cookie, so no reload is needed.
  useEffect(() => onReauthSuccess(() => {
    setVisible(false)
    setReauthPending(false)
  }), [])

  const handleReauthenticate = () => {
    // Must stay synchronous so the user gesture survives the popup blocker.
    window.open(getSsoAuthenticateUrl('/reauth-success'), '_blank', 'noopener')
    // Keep the dialog open until the new tab signals success.
    setReauthPending(true)
  }

  const handleLogout = () => {
    submitOidcLogout()
  }

  const footer = (
    <div className={styles.footer}>
      <button
        type="button"
        className={`${styles.button} ${styles.logoutButton}`}
        onClick={handleLogout}
      >
        <i className="pi pi-sign-out" /> {t('session.logout')}
      </button>
      <button
        type="button"
        className={`${styles.button} ${styles.reauthButton}`}
        onClick={handleReauthenticate}
        autoFocus
      >
        <i className="pi pi-refresh" /> {t('session.reauthenticate')}
      </button>
    </div>
  )

  return (
    <Dialog
      header={t('session.expiredTitle')}
      visible={visible}
      onHide={() => setVisible(false)}
      footer={footer}
      closable={false}
      modal
      style={{ width: '30rem', maxWidth: '90vw' }}
    >
      <p>{reauthPending ? t('session.reauthWaiting') : t('session.expiredMessage')}</p>
    </Dialog>
  )
}
