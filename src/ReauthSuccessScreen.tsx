import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { broadcastReauthSuccess } from './sessionExpiry'
import styles from './ReauthSuccessScreen.module.css'

const AUTO_CLOSE_DELAY_MS = 3000

// Landing page after a successful re-authentication in a new tab. It tells the
// original tab (via BroadcastChannel) that the session is restored, then closes
// itself shortly after so the user can continue where they left off.
export function ReauthSuccessScreen() {
  const { t } = useTranslation()

  useEffect(() => {
    broadcastReauthSuccess()
    const timer = window.setTimeout(() => window.close(), AUTO_CLOSE_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [])

  return (
    <div className={styles.container}>
      <div className={styles.messageBox}>
        <i className={`pi pi-check-circle ${styles.icon}`} aria-hidden="true" />
        <h1 className={styles.title}>{t('session.reauthSuccessTitle')}</h1>
        <p className={styles.message}>{t('session.reauthSuccessMessage')}</p>
        <p className={styles.closing}>{t('session.reauthSuccessClosing')}</p>
      </div>
    </div>
  )
}
