import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/users', () => ({
  usersApi: {
    getTailscale: vi.fn(),
    updateTailscale: vi.fn(),
  },
}))

vi.mock('@/api/auth', () => ({
  authApi: { changePassword: vi.fn() },
}))

// AppShell pulls in router/stores we don't need here — stub it to a passthrough.
vi.mock('@/components/AppShell.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

import { usersApi } from '@/api/users'
import { authApi } from '@/api/auth'
import PreferencesView from '@/views/PreferencesView.vue'
import type { TailscaleConfig } from '@/types'

const baseConfig: TailscaleConfig = {
  enabled: true,
  has_auth_key: true,
  login_server: 'https://login.example.com',
  exit_node: 'node-1',
  accept_routes: true,
  accept_dns: false,
}

describe('PreferencesView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('loads the tailscale config on mount and populates fields', async () => {
    vi.mocked(usersApi.getTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()

    expect(usersApi.getTailscale).toHaveBeenCalledOnce()
    const loginInput = wrapper.find('input[type="url"]').element as HTMLInputElement
    expect(loginInput.value).toBe('https://login.example.com')
    // has_auth_key true -> placeholder hint shown
    const authKeyInputs = wrapper.findAll('input[type="password"]')
    const tsAuthKey = authKeyInputs[authKeyInputs.length - 1].element as HTMLInputElement
    expect(tsAuthKey.placeholder).toContain('configured')
  })

  it('saves tailscale, omitting auth_key when blank', async () => {
    vi.mocked(usersApi.getTailscale).mockResolvedValue(baseConfig)
    vi.mocked(usersApi.updateTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()

    // Submit the tailscale form (second form on the page).
    const forms = wrapper.findAll('form')
    await forms[1].trigger('submit.prevent')
    await flushPromises()

    expect(usersApi.updateTailscale).toHaveBeenCalledOnce()
    const payload = vi.mocked(usersApi.updateTailscale).mock.calls[0][0]
    expect(payload).not.toHaveProperty('auth_key')
    expect(payload.enabled).toBe(true)
    expect(payload.login_server).toBe('https://login.example.com')
  })

  it('includes auth_key when the user types a new one', async () => {
    vi.mocked(usersApi.getTailscale).mockResolvedValue({ ...baseConfig, has_auth_key: false })
    vi.mocked(usersApi.updateTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()

    const pwInputs = wrapper.findAll('input[type="password"]')
    const tsAuthKey = pwInputs[pwInputs.length - 1]
    await tsAuthKey.setValue('tskey-new')

    const forms = wrapper.findAll('form')
    await forms[1].trigger('submit.prevent')
    await flushPromises()

    const payload = vi.mocked(usersApi.updateTailscale).mock.calls[0][0]
    expect(payload.auth_key).toBe('tskey-new')
  })

  it('rejects a too-short new password without calling the API', async () => {
    vi.mocked(usersApi.getTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()

    const pwInputs = wrapper.findAll('input[type="password"]')
    // current, new, confirm are the first three password inputs.
    await pwInputs[0].setValue('oldpassword')
    await pwInputs[1].setValue('short')
    await pwInputs[2].setValue('short')

    await wrapper.findAll('form')[0].trigger('submit.prevent')
    await flushPromises()

    expect(authApi.changePassword).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('at least 8')
  })
})
