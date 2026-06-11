// Detects expired authentication sessions.
//
// When the OIDC session expires, the backend answers API requests with a
// 302 redirect to the identity provider instead of the expected JSON. By
// fetching with `redirect: 'manual'` such a redirect surfaces as an opaque
// response (`type === 'opaqueredirect'`, `status === 0`) which we turn into a
// `SessionExpiredError`. Listeners (e.g. the global dialog) are notified so the
// user can re-authenticate or log out.

export class SessionExpiredError extends Error {
  constructor() {
    super('session.expired')
    this.name = 'SessionExpiredError'
  }
}

type Listener = () => void

const listeners = new Set<Listener>()

export function onSessionExpired(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function notifySessionExpired(): void {
  for (const listener of listeners) {
    try {
      listener()
    } catch (error) {
      console.error('Session-expired listener failed:', error)
    }
  }
}

function isRedirect(response: Response): boolean {
  // Same-origin redirects with `redirect: 'manual'` become opaque (status 0).
  // The status range check is a harmless fallback for non-opaque cases.
  return response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400)
}

// Cross-tab signal: the re-authentication success page (opened in a new tab)
// broadcasts when the user has logged in again, so the original tab can dismiss
// its expired-session dialog. Both tabs are same-origin, so a BroadcastChannel
// works; if it is unavailable the feature simply degrades to no auto-dismiss.
const REAUTH_CHANNEL = 'session-reauth'

export function broadcastReauthSuccess(): void {
  try {
    const channel = new BroadcastChannel(REAUTH_CHANNEL)
    channel.postMessage('reauth-ok')
    channel.close()
  } catch {
    // BroadcastChannel unsupported — nothing to do.
  }
}

export function onReauthSuccess(listener: () => void): () => void {
  let channel: BroadcastChannel | null = null
  try {
    channel = new BroadcastChannel(REAUTH_CHANNEL)
    channel.onmessage = () => listener()
  } catch {
    // BroadcastChannel unsupported.
  }
  return () => channel?.close()
}

// Drop-in replacement for `fetch` that refuses to follow redirects and instead
// reports an expired session.
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, { ...init, redirect: 'manual' })

  if (isRedirect(response)) {
    notifySessionExpired()
    throw new SessionExpiredError()
  }

  return response
}
