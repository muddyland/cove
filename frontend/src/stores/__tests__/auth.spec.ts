import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Mock the auth API module so the store never touches the network.
vi.mock('@/api/auth', () => {
  return {
    authApi: {
      config: vi.fn(),
      setup: vi.fn(),
      login: vi.fn(),
      refresh: vi.fn(),
      me: vi.fn(),
      logout: vi.fn(),
      changePassword: vi.fn(),
    },
  }
})

import { authApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import type { User } from '@/types'

const adminUser: User = {
  id: 1,
  username: 'admin',
  is_admin: true,
  docker_allowed: false,
  auth_provider: 'local',
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

const normalUser: User = { ...adminUser, id: 2, username: 'bob', is_admin: false }

describe('auth store', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('login() stores the token and sets the user', async () => {
    vi.mocked(authApi.login).mockResolvedValue({ access_token: 'tok-123' })
    vi.mocked(authApi.me).mockResolvedValue(normalUser)

    const auth = useAuthStore()
    await auth.login('bob', 'pw')

    expect(authApi.login).toHaveBeenCalledWith('bob', 'pw')
    expect(auth.token).toBe('tok-123')
    // Never persisted: a same-origin workspace frame could read localStorage.
    expect(localStorage.getItem('cove_token')).toBeNull()
    expect(auth.user).toEqual(normalUser)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('setToken keeps the token in memory only', () => {
    const auth = useAuthStore()
    auth.setToken('abc')
    expect(auth.token).toBe('abc')
    expect(localStorage.getItem('cove_token')).toBeNull()
  })

  it('logout() clears token + user and removes from localStorage', async () => {
    vi.mocked(authApi.logout).mockResolvedValue(undefined)
    const auth = useAuthStore()
    auth.setToken('abc')
    auth.user = normalUser

    await auth.logout()

    expect(authApi.logout).toHaveBeenCalledOnce()
    expect(auth.token).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('cove_token')).toBeNull()
    expect(auth.isAuthenticated).toBe(false)
  })

  it('logout() still clears local state even if the API call fails', async () => {
    // logout() uses try/finally (no catch), so the rejection propagates while
    // the finally block still clears local auth state.
    vi.mocked(authApi.logout).mockRejectedValue(new Error('network'))
    const auth = useAuthStore()
    auth.setToken('abc')
    auth.user = normalUser

    await expect(auth.logout()).rejects.toThrow('network')
    expect(auth.token).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('cove_token')).toBeNull()
  })

  it('clear() removes token + user', () => {
    const auth = useAuthStore()
    auth.setToken('abc')
    auth.user = normalUser
    auth.clear()
    expect(auth.token).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('cove_token')).toBeNull()
  })

  it('isAuthenticated reflects user presence', () => {
    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)
    auth.user = normalUser
    expect(auth.isAuthenticated).toBe(true)
  })

  it('isAdmin computed reflects user.is_admin', () => {
    const auth = useAuthStore()
    expect(auth.isAdmin).toBe(false)
    auth.user = normalUser
    expect(auth.isAdmin).toBe(false)
    auth.user = adminUser
    expect(auth.isAdmin).toBe(true)
  })

  it('ignores (and scrubs) a token a pre-1.1 build persisted', async () => {
    localStorage.setItem('cove_token', 'persisted')
    setActivePinia(createPinia())
    const auth = useAuthStore()
    expect(auth.token).toBeNull()
    vi.mocked(authApi.config).mockResolvedValue({
      oidc_enabled: false, oidc_provider_name: 'SSO', needs_setup: false, oidc_only: false,
    })
    vi.mocked(authApi.refresh).mockRejectedValue(new Error('no session'))
    await auth.init()
    expect(localStorage.getItem('cove_token')).toBeNull()
  })
})
