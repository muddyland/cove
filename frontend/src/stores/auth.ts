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
    isAuthenticated, isAdmin, needsSetup, oidcEnabled, oidcProviderName,
    setToken, clear, loadConfig, init, login, setup, logout,
  }
})
