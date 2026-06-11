// Shared helpers for the OIDC login/logout flows. These intentionally use full
// page navigations (link / form submit) so the browser leaves the SPA and the
// redirects to the identity provider are followed normally.

function getCookieValue(name: string): string {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) {
    return parts.pop()?.split(';').shift() ?? ''
  }
  return ''
}

// URL of the "login with SSO" flow, returning to the current location afterwards.
export function getSsoAuthenticateUrl(next?: string): string {
  const target = next ?? `${location.pathname}${location.search}`
  return `/oidc/authenticate/?next=${encodeURIComponent(target)}`
}

// Logs the user out via the OIDC logout endpoint, which in turn redirects to the
// Keycloak logout page. The CSRF token is read directly from the cookie so the
// logout does not depend on a (possibly failing) token refresh.
export function submitOidcLogout(next: string = '/'): void {
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
  nextInput.value = next
  form.appendChild(nextInput)

  document.body.appendChild(form)
  form.submit()
}
