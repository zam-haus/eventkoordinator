import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ command }) => {
  // When building for Django's test/runserver setup, assets must be served
  // from /static/spa/ (Django's staticfiles prefix).  Set VITE_DJANGO_BASE=1
  // to opt in.  All other builds (Docker/nginx, dev server) keep base as '/'.
  const djangoBuild = command === 'build' && !!process.env.VITE_DJANGO_BASE
  // print mode
    console.log(`Building for ${djangoBuild ? 'Django' : 'dev server'} mode...`)
  // Set VITE_HIDE_PASSWORD_AUTH=1 to remove the username/password login form and
  // show only SSO login (useful when password auth is disabled on the server).
  const hidePasswordAuth = !!process.env.VITE_HIDE_PASSWORD_AUTH
  return {
    define: {
      __HIDE_PASSWORD_AUTH__: hidePasswordAuth,
    },
    base: djangoBuild ? '/static/' : '/',
    plugins: [react()],
    css: {
      modules: {
        localsConvention: 'camelCase'
      }
    },
    server: {
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: false,
        },
        '/oidc': {
          target: 'http://localhost:8000',
          changeOrigin: false,
        },
        '/media': {
          target: 'http://localhost:8000',
          changeOrigin: false,
        },
        '/admin': {
          target: 'http://localhost:8000',
          changeOrigin: false,
        },
        '/static': {
          target: 'http://localhost:8000',
          changeOrigin: false,
        },
      },
    },
  }
})
