import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog } from 'primereact/dialog'
import { Button } from 'primereact/button'
import { onSessionExpired } from './sessionExpiry'
import { getSsoAuthenticateUrl, submitOidcLogout } from './oidcAuth'

// Global popup shown when an API call detects that the authentication session
// has expired (the backend redirected the request instead of answering it).
export function SessionExpiredDialog() {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(false)

  useEffect(() => onSessionExpired(() => setVisible(true)), [])

  const handleReauthenticate = () => {
    // Must stay synchronous so the user gesture survives the popup blocker.
    window.open(getSsoAuthenticateUrl(), '_blank', 'noopener')
    setVisible(false)
  }

  const handleLogout = () => {
    submitOidcLogout()
  }

  const footer = (
    <div>
      <Button
        label={t('session.logout')}
        icon="pi pi-sign-out"
        className="p-button-text"
        onClick={handleLogout}
      />
      <Button
        label={t('session.reauthenticate')}
        icon="pi pi-refresh"
        onClick={handleReauthenticate}
        autoFocus
      />
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
      <p>{t('session.expiredMessage')}</p>
    </Dialog>
  )
}
