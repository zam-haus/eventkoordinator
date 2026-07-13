import { useState, useEffect } from 'react'
import { getUserPermissions, type UserPermissions } from './api'

const AUTH_CHANGED_EVENT = 'app:auth-changed'

// Cross-tab notification: sudo mode / login state live in the shared session,
// so a change in one tab must refresh permissions in all tabs.
const authChannel: BroadcastChannel | null =
  typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(AUTH_CHANGED_EVENT) : null

export function notifyAuthChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT))
  }
  authChannel?.postMessage('auth-changed')
}

export function usePermissions() {
  const [permissions, setPermissions] = useState<UserPermissions | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let isMounted = true

    const loadPermissions = async () => {
      setLoading(true)
      try {
        const perms = await getUserPermissions()
        if (isMounted) {
          setPermissions(perms)
        }
      } catch (error) {
        console.error('Failed to load permissions:', error)
        if (isMounted) {
          // Set default unauthenticated permissions
          setPermissions({
            is_authenticated: false,
            is_staff: false,
            is_superuser: false,
            is_active: false,
            sudo_mode: false,
            permissions: [],
          })
        }
      } finally {
        if (isMounted) {
          setLoading(false)
        }
      }
    }

    const handleAuthChanged = () => {
      void loadPermissions()
    }

    void loadPermissions()
    window.addEventListener(AUTH_CHANGED_EVENT, handleAuthChanged)
    authChannel?.addEventListener('message', handleAuthChanged)

    return () => {
      isMounted = false
      window.removeEventListener(AUTH_CHANGED_EVENT, handleAuthChanged)
      authChannel?.removeEventListener('message', handleAuthChanged)
    }
  }, [])

  const hasPermission = (permission: string): boolean => {
    if (!permissions) return false
    if (permissions.is_superuser) return true
    return permissions.permissions.includes(permission)
  }

  const canView = (model: string): boolean => {
    return hasPermission(`view_${model}`) || hasPermission(`change_${model}`) || hasPermission(`add_${model}`)
  }

  const canBrowse = (model: string): boolean => {
    return hasPermission(`browse_${model}`) || canView(model)
  }

  const canAdd = (model: string): boolean => {
    return hasPermission(`add_${model}`)
  }

  const canChange = (model: string): boolean => {
    return hasPermission(`change_${model}`)
  }

  const canDelete = (model: string): boolean => {
    return hasPermission(`delete_${model}`)
  }

  return {
    permissions,
    loading,
    hasPermission,
    canView,
    canBrowse,
    canAdd,
    canChange,
    canDelete,
  }
}
