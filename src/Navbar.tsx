import { useState, useRef, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Menubar } from 'primereact/menubar'
import type { MenuItem } from 'primereact/menuitem'
import { initializeCsrfToken, login as apiLogin, fetchSiteConfig, type SiteConfig } from './api'
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
  const navigate = useNavigate()
  const { canBrowse, permissions } = usePermissions()

  useEffect(() => {
    fetchSiteConfig().then(setSiteConfig).catch(() => { /* non-critical */ })
  }, [])

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
      const loggedInUser = await apiLogin(loginFormData.username, loginFormData.password)
      onLogin(loggedInUser)
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

  const getCookieValue = (name: string): string => {
    const value = `; ${document.cookie}`
    const parts = value.split(`; ${name}=`)
    if (parts.length === 2) return parts.pop()?.split(';').shift() ?? ''
    return ''
  }

  const handleLogout = async () => {
    onLogout()
    setIsDropdownOpen(false)
    await initializeCsrfToken()
    const form = document.createElement('form')
    form.method = 'POST'
    form.action = '/oidc/logout/'
    const csrfInput = document.createElement('input')
    csrfInput.type = 'hidden'
    csrfInput.name = 'csrfmiddlewaretoken'
    csrfInput.value = getCookieValue('csrftoken')
    form.appendChild(csrfInput)
    const nextInput = document.createElement('input')
    nextInput.type = 'hidden'
    nextInput.name = 'next'
    nextInput.value = '/'
    form.appendChild(nextInput)
    document.body.appendChild(form)
    form.submit()
  }

  const handleSsoLogin = () => {
    const next = `${location.pathname}${location.search}`
    window.location.href = `/oidc/authenticate/?next=${encodeURIComponent(next)}`
  }

  // Renders a dropdown sub-item as a React Router Link with active styling
  const subLinkItem = (label: string, to: string, isActive: boolean): MenuItem => ({
    label,
    template: (_item, options) => (
      <a
        href={to}
        className={isActive ? styles.activeSubLink : styles.subLink}
        onClick={(e) => {
          if (!e.ctrlKey && !e.metaKey && !e.shiftKey) {
            e.preventDefault()
            options.onClick?.(e)
            navigate(to)
          }
        }}
      >
        {label}
      </a>
    ),
  })

  // Renders a top-level nav item as a React Router Link with active styling
  const navLinkItem = (label: string, to: string, isActive: boolean): MenuItem => ({
    label,
    template: (_item, options) => (
      <a
        href={to}
        className={isActive ? styles.activeNavLink : styles.navLink}
        onClick={(e) => {
          if (!e.ctrlKey && !e.metaKey && !e.shiftKey) {
            e.preventDefault()
            options.onClick?.(e)
            navigate(to)
          }
        }}
      >
        {label}
      </a>
    ),
  })

  const navItems: MenuItem[] = []

  if (canBrowse('series')) {
    navItems.push(navLinkItem(
      t('nav.coordinator'),
      '/coordinator',
      location.pathname.startsWith('/coordinator'),
    ))
  }
  if (canBrowse('proposal')) {
    navItems.push(navLinkItem(
      t('nav.proposalEditor'),
      '/proposal-editor',
      location.pathname === '/proposal-editor',
    ))
    navItems.push(navLinkItem(
      t('nav.proposalDashboard'),
      '/proposal-dashboard',
      location.pathname === '/proposal-dashboard',
    ))
  }
  if (permissions?.is_staff) {
    const udmActive = location.pathname.startsWith('/udm-admin')
    navItems.push({
      label: 'UDM Admin',
      template: (_item, options) => (
        <a
          className={`${options.className} ${udmActive ? styles.activeNavLink : ''}`}
          onClick={options.onClick}
          role="menuitem"
          aria-haspopup="true"
        >
          <span className={options.labelClassName}>UDM Admin</span>
          <span className="p-submenu-icon pi pi-angle-down" />
        </a>
      ),
      items: [
        subLinkItem('Field Configs', '/udm-admin/configs', location.pathname === '/udm-admin/configs' || location.pathname === '/udm-admin'),
        subLinkItem('UDM Types', '/udm-admin/types', location.pathname === '/udm-admin/types'),
        subLinkItem('Rego Policies', '/udm-admin/policies', location.pathname === '/udm-admin/policies'),
        subLinkItem('Bulk Migration', '/udm-admin/migrations', location.pathname === '/udm-admin/migrations'),
        subLinkItem('Export / Import', '/udm-admin/bundle', location.pathname === '/udm-admin/bundle'),
        subLinkItem('Workflow Editor', '/udm-admin/workflow', location.pathname === '/udm-admin/workflow'),
      ],
    })
  }
  navItems.push(navLinkItem(
    'UDM Dashboard',
    '/udm-dashboard',
    location.pathname.startsWith('/udm-dashboard'),
  ))
  navItems.push(navLinkItem(
    'UDM Entities',
    '/udm-entity',
    location.pathname.startsWith('/udm-entity'),
  ))
  if (permissions?.is_staff) {
    navItems.push({ label: t('nav.adminPanel'), url: '/admin/' })
  }

  const start = (
    <Link to="/" className={styles.logo} aria-label={`${t('nav.appName')} – ${t('nav.goToHome')}`}>
      <h1>{t('nav.appName')}</h1>
    </Link>
  )

  const end = (
    <div className={styles.endSection}>
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
              <form
                className={styles.loginForm}
                onSubmit={handleLoginSubmit}
                aria-label="Login form"
              >
                <button
                  type="button"
                  className={styles.ssoButton}
                  onClick={handleSsoLogin}
                  aria-label={t('nav.loginWithSso')}
                >
                  {t('nav.loginWithSso')}
                </button>
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
                    onChange={e => setLoginFormData({ ...loginFormData, username: e.target.value })}
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
                    onChange={e => setLoginFormData({ ...loginFormData, password: e.target.value })}
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
  )

  return (
    <Menubar
      className={styles.menubar}
      model={navItems}
      start={start}
      end={end}
      aria-label="Main navigation"
    />
  )
}
