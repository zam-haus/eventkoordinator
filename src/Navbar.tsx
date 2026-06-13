import { useState, useRef, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { initializeCsrfToken, login as apiLogin, fetchSiteConfig, type SiteConfig } from './api'
import { getSsoAuthenticateUrl, submitOidcLogout } from './oidcAuth'
import { usePermissions } from './usePermissions'
import { translateApiError } from './apiError'
import { useTranslation } from 'react-i18next'
import i18n from './i18n'
import type { User } from './api'
import styles from './Navbar.module.css'

interface NavbarProps {
  user: User | null
  onLogin: (user: User) => void
  onLogout: () => void
}

export function Navbar({ user, onLogin, onLogout }: NavbarProps) {
  const { t } = useTranslation()
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [loginFormData, setLoginFormData] = useState({ username: '', password: '' })
  const [loginError, setLoginError] = useState<string | null>(null)
  const [isLoggingIn, setIsLoggingIn] = useState(false)
  const [siteConfig, setSiteConfig] = useState<SiteConfig | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const { canBrowse, permissions } = usePermissions()
  const hasNavLinks = canBrowse('series') || canBrowse('proposal') || !!permissions?.is_staff

  useEffect(() => {
    fetchSiteConfig().then(setSiteConfig).catch(() => { /* non-critical */ })
  }, [])

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoginError(null)
    setIsLoggingIn(true)

    try {
      if (!loginFormData.username.trim() || !loginFormData.password.trim()) {
        setLoginError(t('api.common.invalidRequest'))
        return
      }

      const user = await apiLogin(loginFormData.username, loginFormData.password)
      onLogin(user)
      setLoginFormData({ username: '', password: '' })
      setIsDropdownOpen(false)
    } catch (error) {
      const code = error instanceof Error ? error.message : 'api.common.internalError'
      setLoginError(translateApiError(code))
      console.error('Login error:', error)
    } finally {
      setIsLoggingIn(false)
    }
  }

  const handleLogout = async () => {
    onLogout()
    setIsDropdownOpen(false)

    // Refresh the CSRF cookie before the OIDC logout endpoint (expects POST).
    await initializeCsrfToken()
    submitOidcLogout()
  }

  const handleSsoLogin = () => {
    window.location.href = getSsoAuthenticateUrl()
  }

  return (
    <nav className={styles.navbar} aria-label="Main navigation">
      <div className={styles.container}>
        <Link to="/" className={styles.logo} aria-label={`${t('nav.appName')} – ${t('nav.goToHome')}`}>
          <h1>{t('nav.appName')}</h1>
        </Link>

        <div className={styles.navLinks} role="list">
          {canBrowse('series') && (
            <Link
              role="listitem"
              className={location.pathname.startsWith('/coordinator') ? styles.activeNavLink : styles.navLink}
              to="/coordinator"
              aria-current={location.pathname.startsWith('/coordinator') ? 'page' : undefined}
            >
              {t('nav.coordinator')}
            </Link>
          )}
          {canBrowse('proposal') && (
            <Link
              role="listitem"
              className={location.pathname === '/proposal-editor' ? styles.activeNavLink : styles.navLink}
              to="/proposal-editor"
              aria-current={location.pathname === '/proposal-editor' ? 'page' : undefined}
            >
              {t('nav.proposalEditor')}
            </Link>
          )}
          {canBrowse('proposal') && (
            <Link
              role="listitem"
              className={location.pathname === '/proposal-dashboard' ? styles.activeNavLink : styles.navLink}
              to="/proposal-dashboard"
              aria-current={location.pathname === '/proposal-dashboard' ? 'page' : undefined}
            >
              {t('nav.proposalDashboard')}
            </Link>
          )}
          {permissions?.is_staff && (
            <a
              role="listitem"
              className={styles.navLink}
              href="/admin/"
              aria-label={t('nav.adminLink')}
            >
              {t('nav.adminPanel')}
            </a>
          )}
        </div>

        <div className={styles.langSwitcher} aria-label="Language switcher">
          <button
            type="button"
            className={`${styles.langButton} ${i18n.language.startsWith('en') ? styles.langButtonActive : ''}`}
            onClick={() => void i18n.changeLanguage('en')}
            aria-label="Switch to English"
            aria-pressed={i18n.language.startsWith('en')}
          >
            🇬🇧
          </button>
          <button
            type="button"
            className={`${styles.langButton} ${i18n.language.startsWith('de') ? styles.langButtonActive : ''}`}
            onClick={() => void i18n.changeLanguage('de')}
            aria-label="Auf Deutsch wechseln"
            aria-pressed={i18n.language.startsWith('de')}
          >
            🇩🇪
          </button>
        </div>

        <div className={styles.menu} ref={dropdownRef}>
          <button
            type="button"
            className={styles.menuButton}
            onClick={() => setIsDropdownOpen(!isDropdownOpen)}
            aria-expanded={isDropdownOpen}
            aria-haspopup="true"
            aria-label="User menu"
          >
            <span className={styles.userIcon} aria-hidden="true">👤</span>
            <span className={styles.username}>
              {user ? user.username : t('nav.guest')}
            </span>
            <span className={styles.chevron} aria-hidden="true">▼</span>
          </button>

          {isDropdownOpen && (
            <div className={styles.dropdown} role="menu" aria-label="User options">
              {hasNavLinks && (
                <div className={styles.dropdownNavSection}>
                  {canBrowse('series') && (
                    <Link
                      to="/coordinator"
                      className={location.pathname.startsWith('/coordinator') ? styles.dropdownNavLinkActive : styles.menuLink}
                      onClick={() => setIsDropdownOpen(false)}
                      role="menuitem"
                      aria-current={location.pathname.startsWith('/coordinator') ? 'page' : undefined}
                    >
                      {t('nav.coordinator')}
                    </Link>
                  )}
                  {canBrowse('proposal') && (
                    <Link
                      to="/proposal-editor"
                      className={location.pathname === '/proposal-editor' ? styles.dropdownNavLinkActive : styles.menuLink}
                      onClick={() => setIsDropdownOpen(false)}
                      role="menuitem"
                      aria-current={location.pathname === '/proposal-editor' ? 'page' : undefined}
                    >
                      {t('nav.proposalEditor')}
                    </Link>
                  )}
                  {canBrowse('proposal') && (
                    <Link
                      to="/proposal-dashboard"
                      className={location.pathname === '/proposal-dashboard' ? styles.dropdownNavLinkActive : styles.menuLink}
                      onClick={() => setIsDropdownOpen(false)}
                      role="menuitem"
                      aria-current={location.pathname === '/proposal-dashboard' ? 'page' : undefined}
                    >
                      {t('nav.proposalDashboard')}
                    </Link>
                  )}
                  {permissions?.is_staff && (
                    <a
                      href="/admin/"
                      className={styles.menuLink}
                      role="menuitem"
                      aria-label={t('nav.adminLink')}
                      onClick={() => setIsDropdownOpen(false)}
                    >
                      {t('nav.adminPanel')}
                    </a>
                  )}
                  <hr className={styles.divider} />
                </div>
              )}
              {user ? (
                <>
                  <div className={styles.userInfo}>
                    <span className={styles.infoLabel}>{t('nav.loggedInAs')}</span>
                    <span className={styles.infoValue}>{user.username}</span>
                  </div>
                  <hr className={styles.divider} />
                  {siteConfig && (
                    <a
                      href={siteConfig.account_management_url}
                      className={styles.menuLink}
                      target="_blank"
                      rel="noopener noreferrer"
                      role="menuitem"
                    >
                      {t('nav.manageAccount')}
                    </a>
                  )}
                  <hr className={styles.divider} />
                  <button
                    type="button"
                    className={styles.logoutButton}
                    onClick={handleLogout}
                    role="menuitem"
                  >
                    {t('common.logout')}
                  </button>
                </>
              ) : (
                <div className={styles.loginForm}>
                  <button
                    type="button"
                    className={styles.ssoButton}
                    onClick={handleSsoLogin}
                    aria-label={t('nav.loginWithSso')}
                  >
                    {t('nav.loginWithSso')}
                  </button>
                  {!__HIDE_PASSWORD_AUTH__ && (
                    <form
                      onSubmit={handleLoginSubmit}
                      aria-label="Login form"
                    >
                      <div className={styles.ssoHint} aria-hidden="true">{t('nav.ssoHint')}</div>
                      {loginError && (
                        <div className={styles.errorMessage} role="alert">{loginError}</div>
                      )}
                      <div className={styles.formGroup}>
                        <label htmlFor="username" className={styles.label}>
                          {t('nav.username')}
                        </label>
                        <input
                          id="username"
                          type="text"
                          className={styles.input}
                          placeholder={t('nav.enterUsername')}
                          value={loginFormData.username}
                          onChange={(e) =>
                            setLoginFormData({ ...loginFormData, username: e.target.value })
                          }
                          disabled={isLoggingIn}
                          autoFocus
                          autoComplete="username"
                        />
                      </div>
                      <div className={styles.formGroup}>
                        <label htmlFor="password" className={styles.label}>
                          {t('nav.password')}
                        </label>
                        <input
                          id="password"
                          type="password"
                          className={styles.input}
                          placeholder={t('nav.enterPassword')}
                          value={loginFormData.password}
                          onChange={(e) =>
                            setLoginFormData({ ...loginFormData, password: e.target.value })
                          }
                          disabled={isLoggingIn}
                          autoComplete="current-password"
                        />
                      </div>
                      <button
                        type="submit"
                        className={styles.loginButton}
                        disabled={!loginFormData.username.trim() || !loginFormData.password.trim() || isLoggingIn}
                        aria-busy={isLoggingIn}
                      >
                        {isLoggingIn ? t('nav.loggingIn') : t('common.login')}
                      </button>
                    </form>
                  )}
                </div>
              )}
              {siteConfig && (
                <>
                  <hr className={styles.divider} />
                  <div className={styles.menuFooterLinks}>
                    <a
                      href={siteConfig.imprint_url}
                      className={styles.menuLink}
                      target="_blank"
                      rel="noopener noreferrer"
                      role="menuitem"
                    >
                      {t('nav.imprint')}
                    </a>
                    <a
                      href={siteConfig.privacy_policy_url}
                      className={styles.menuLink}
                      target="_blank"
                      rel="noopener noreferrer"
                      role="menuitem"
                    >
                      {t('nav.privacyPolicy')}
                    </a>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </nav>
  )
}
