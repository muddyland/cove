import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/auth'
import type { User, AuthConfig } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const token = ref<string | null>(localStorage.getItem('cove_token'))
  const config = ref<AuthConfig | null>(null)

  const isAuthenticated = computed(() => !!user.value)
  const isAdmin = computed(() => user.value?.is_admin ?? false)
  const needsSetup = computed(() => config.value?.needs_setup ?? false)
  const oidcEnabled = computed(() => config.value?.oidc_enabled ?? false)
  const oidcProviderName = computed(() => config.value?.oidc_provider_name ?? 'SSO')
  const oidcOnly = computed(() => config.value?.oidc_only ?? false)

  function setToken(t: string) {
    token.value = t
    localStorage.setItem('cove_token', t)
  }

  function clear() {
    user.value = null
    token.value = null
    localStorage.removeItem('cove_token')
  }

  async function loadConfig() {
    config.value = await authApi.config()
  }

  async function init() {
    await loadConfig()
    // Resume a cookie-based session when there's no stored bearer token. This
    // covers OIDC logins (the callback sets httpOnly cookies but no localStorage
    // token) and returning users whose refresh cookie is still valid.
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

  async function login(username: string, password: string) {
    const { access_token } = await authApi.login(username, password)
    setToken(access_token)
    user.value = await authApi.me()
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
    setToken, clear, loadConfig, init, login, setup, logout,
  }
})
