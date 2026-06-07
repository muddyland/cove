import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/users', () => ({
  usersApi: {
    getTailscale: vi.fn(),
    updateTailscale: vi.fn(),
    getGluetun: vi.fn(),
    updateGluetun: vi.fn(),
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
import { useAuthStore } from '@/stores/auth'
import PreferencesView from '@/views/PreferencesView.vue'
import type { GluetunConfig, TailscaleConfig, User } from '@/types'

const baseConfig: TailscaleConfig = {
  enabled: true,
  has_auth_key: true,
  login_server: 'https://login.example.com',
}

const defaultGluetun: GluetunConfig = {
  enabled: false,
  vpn_type: 'openvpn',
  has_config: false,
  config_filename: null,
  has_wireguard_private_key: false,
  has_openvpn_user: false,
  has_openvpn_password: false,
}

function setUser(provider: string) {
  useAuthStore().user = {
    id: 1, username: 'me', is_admin: false, auth_provider: provider,
  } as User
}

// The tailscale form is the one holding the (url) login-server input; its single
// password input is the auth key. Robust against extra forms/password fields.
function tsAuthKeyInput(wrapper: ReturnType<typeof mount>) {
  const tsForm = wrapper.findAll('form').find(f => f.find('input[type="url"]').exists())!
  return tsForm.find('input[type="password"]')
}

describe('PreferencesView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    setUser('local')
    vi.mocked(usersApi.getGluetun).mockResolvedValue(defaultGluetun)
    vi.mocked(usersApi.updateGluetun).mockResolvedValue(defaultGluetun)
  })

  it('loads the tailscale config on mount and populates fields', async () => {
    vi.mocked(usersApi.getTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()

    expect(usersApi.getTailscale).toHaveBeenCalledOnce()
    const loginInput = wrapper.find('input[type="url"]').element as HTMLInputElement
    expect(loginInput.value).toBe('https://login.example.com')
    // has_auth_key true -> placeholder hint shown
    const tsAuthKey = tsAuthKeyInput(wrapper).element as HTMLInputElement
    expect(tsAuthKey.placeholder).toContain('configured')
  })

  it('saves tailscale, omitting auth_key when blank', async () => {
    vi.mocked(usersApi.getTailscale).mockResolvedValue(baseConfig)
    vi.mocked(usersApi.updateTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()

    const tsForm = wrapper.findAll('form').find(f => f.find('input[type="url"]').exists())!
    await tsForm.trigger('submit.prevent')
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

    await tsAuthKeyInput(wrapper).setValue('tskey-new')
    const tsForm = wrapper.findAll('form').find(f => f.find('input[type="url"]').exists())!
    await tsForm.trigger('submit.prevent')
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

  it('shows the change-password panel for local users', async () => {
    vi.mocked(usersApi.getTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()
    expect(wrapper.text()).toContain('CHANGE PASSWORD')
  })

  it('hides the change-password panel for SSO (OIDC) users', async () => {
    setUser('oidc')
    vi.mocked(usersApi.getTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()
    expect(wrapper.text()).not.toContain('CHANGE PASSWORD')
    // Tailscale panel is still present.
    expect(wrapper.text()).toContain('TAILSCALE')
  })

  it('loads and saves the gluetun config', async () => {
    vi.mocked(usersApi.getTailscale).mockResolvedValue(baseConfig)
    const wrapper = mount(PreferencesView)
    await flushPromises()
    expect(usersApi.getGluetun).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('GLUETUN')

    // Submit the gluetun form (the one with a file input).
    const gForm = wrapper.findAll('form').find(f => f.find('input[type="file"]').exists())!
    await gForm.trigger('submit.prevent')
    await flushPromises()
    expect(usersApi.updateGluetun).toHaveBeenCalledOnce()
    const payload = vi.mocked(usersApi.updateGluetun).mock.calls[0][0]
    expect(payload.vpn_type).toBe('openvpn')
    // No file chosen -> config_file omitted (sentinel leaves it unchanged).
    expect(payload).not.toHaveProperty('config_file')
  })
})
