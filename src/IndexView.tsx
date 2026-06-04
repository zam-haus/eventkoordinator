import { useState, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Navbar } from './Navbar'
import { DefaultScreen } from './DefaultScreen'
import { MainView } from './MainView'
import { ProposalEventView } from './ProposalEventView'
import { SyncDiff } from './SyncDiff'
import { getCurrentUser, initializeCsrfToken, type User } from './api'
import { usePermissions, notifyAuthChanged } from './usePermissions'
import i18n from './i18n'
import styles from './IndexView.module.css'
import {ProposalSelectionPanel} from "./ProposalSelectionPanel.tsx";
import { ProposalDashboard } from './ProposalDashboard'
import { UdmAdminPage } from './UdmAdminPage'
import { UdmEntityEditor, UdmEntityPanel } from './UdmEntityEditor'
import { UdmDashboard } from './UdmDashboard'

export function IndexView() {
  const { t } = useTranslation()
  const [user, setUser] = useState<User | null>(null)
  const { canBrowse, permissions, loading: permissionsLoading } = usePermissions()

  useEffect(() => {
    const initializeApp = async () => {
      await initializeCsrfToken()
      try {
        const currentUser = await getCurrentUser()
        setUser(currentUser)
        // Set locale from user profile
        const locale = navigator.language.split('-')[0]
        i18n.changeLanguage(locale)
      } catch {
        console.log('User not authenticated')
      }
    }
    initializeApp()
  }, [])

  const handleLogin = (authenticatedUser: User) => {
    setUser(authenticatedUser)
    notifyAuthChanged()
    // Set locale from user profile
    const locale = navigator.language.split('-')[0]
    i18n.changeLanguage(locale)
  }

  const handleLogout = () => {
    setUser(null)
    notifyAuthChanged()
  }

  return (
    <div className={styles.layout}>
      <Navbar user={user} onLogin={handleLogin} onLogout={handleLogout} />
      <main className={styles.main}>
        <Routes>
          <Route path="/" element={<DefaultScreen />} />
          <Route
            path="/coordinator/:seriesId?/:eventId?"
            element={
              permissionsLoading ? null : canBrowse('series') ? <MainView /> : (
                <div style={{ padding: '2rem', textAlign: 'center', color: '#666' }}>
                  <p style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>{t('common.accessDenied')}</p>
                  <p>{t('common.noPermissionBrowseSeries')}</p>
                </div>
              )
            }
          />
          <Route
            path="/proposal-dashboard"
            element={
              permissionsLoading ? null : canBrowse('proposal') ? <ProposalDashboard /> : (
                <div style={{ padding: '2rem', textAlign: 'center', color: '#666' }}>
                  <p style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>{t('common.accessDenied')}</p>
                  <p>{t('common.noPermissionBrowseProposal')}</p>
                </div>
              )
            }
          />
          <Route
            path="/proposal-editor/:proposalId?"
            element={
              permissionsLoading ? null : canBrowse('proposal') ? <ProposalSelectionPanel /> : (
                <div style={{ padding: '2rem', textAlign: 'center', color: '#666' }}>
                  <p style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>{t('common.accessDenied')}</p>
                  <p>{t('common.noPermissionBrowseProposal')}</p>
                </div>
              )
            }
          />
          <Route
            path="/sync/diff/:seriesId/:eventId/:targetId"
            element={<SyncDiff />}
          />
          <Route
            path="/udm-admin/:tab?"
            element={
              permissionsLoading ? null : permissions?.is_staff ? <UdmAdminPage /> : (
                <div style={{ padding: '2rem', textAlign: 'center', color: '#666' }}>
                  <p style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>{t('common.accessDenied')}</p>
                </div>
              )
            }
          />
          <Route path="/udm-dashboard" element={<UdmDashboard />} />
          <Route path="/udm-entity" element={<UdmEntityPanel />} />
          <Route path="/udm-entity/:entityId" element={<UdmEntityEditor />} />
<Route
            path="/proposal/:proposalId/event/:eventId"
            element={
              permissionsLoading ? null : canBrowse('proposal') ? <ProposalEventView /> : (
                <div style={{ padding: '2rem', textAlign: 'center', color: '#666' }}>
                  <p style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>{t('common.accessDenied')}</p>
                  <p>{t('common.noPermissionBrowseProposal')}</p>
                </div>
              )
            }
          />
        </Routes>
      </main>
    </div>
  )
}
