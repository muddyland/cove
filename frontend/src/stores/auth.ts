import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/auth'
import type { User, AuthConfig, LoginStep } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  // Held in memory only. It used to be persisted in localStorage, where any
  // same-origin script — including a workspace stream framed under
  // /workspace/{id}/ in subpath mode — could read it. A reload resumes the
  // session from the httpOnly refresh cookie instead (see init()).
  const token = ref<string | null>(null)
  const config = ref<AuthConfig | null>(null)

  const isAuthenticated = computed(() => !!user.value)
  const isAdmin = computed(() => user.value?.is_admin ?? false)
  const needsSetup = computed(() => config.value?.needs_setup ?? false)
  const oidcEnabled = computed(() => config.value?.oidc_enabled ?? false)
  const oidcProviderName = computed(() => config.value?.oidc_provider_name ?? 'SSO')
  const oidcOnly = computed(() => config.value?.oidc_only ?? false)

  function setToken(t: string) {
    token.value = t
  }

  function clear() {
    user.value = null
    token.value = null
    // Drop a token a pre-1.1 build may have left behind.
    try { localStorage.removeItem('cove_token') } catch {}
    // Screen previews are pictures of the user's desktops — drop them (and
    // revoke their object URLs) so nothing survives a logout or a session
    // expiry on a shared machine. Imported lazily: this store is initialised
    // before the previews store exists.
    import('./previews')
      .then(m => m.usePreviewsStore().clearAll())
      .catch(() => {})
  }

  async function loadConfig() {
    config.value = await authApi.config()
  }

  async function init() {
    await loadConfig()
    try { localStorage.removeItem('cove_token') } catch {}
    // Resume a cookie-based session: the access token is never persisted, so
    // every page load (OIDC callback, reload, returning user) goes through the
    // httpOnly refresh cookie.
    if (!token.value) {
      try {
        const { access_token } = await authApi.refresh()
        setToken(access_token)
      } catch {
        /* no active session — stay anonymous */
      }
    }
    if (token.value) {
      try {
        user.value = await authApi.me()
      } catch {
        clear()
      }
    }
  }

  /** Finish signing in with a session token from any sign-in step. */
  async function completeSignIn(accessToken: string) {
    setToken(accessToken)
    user.value = await authApi.me()
  }

  /** Password step. Returns null when signed in, or what the next step needs
   *  (a second factor, or setting one up because an admin requires it). */
  async function login(username: string, password: string): Promise<Exclude<LoginStep, { access_token: string }> | null> {
    const step = await authApi.login(username, password)
    if ('access_token' in step) {
      await completeSignIn(step.access_token)
      return null
    }
    return step
  }

  async function setup(username: string, password: string) {
    const { access_token } = await authApi.setup(username, password)
    setToken(access_token)
    user.value = await authApi.me()
    if (config.value) config.value.needs_setup = false
  }

  async function logout() {
    try {
      await authApi.logout()
    } finally {
      clear()
    }
  }

  return {
    user, token, config,
    isAuthenticated, isAdmin, needsSetup, oidcEnabled, oidcProviderName, oidcOnly,
    setToken, clear, loadConfig, init, login, completeSignIn, setup, logout,
  }
})
