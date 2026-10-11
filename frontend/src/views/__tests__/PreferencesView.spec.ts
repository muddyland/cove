import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/users', () => ({
  usersApi: {
    listVpnProfiles: vi.fn(),
    listTailscaleProfiles: vi.fn(),
    listVpnProviders: vi.fn(),
    createVpnProfile: vi.fn(),
    updateVpnProfile: vi.fn(),
    deleteVpnProfile: vi.fn(),
    createTailscaleProfile: vi.fn(),
    updateTailscaleProfile: vi.fn(),
    deleteTailscaleProfile: vi.fn(),
    getSshKey: vi.fn(),
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
import type { TailscaleProfile, User, VpnProfile } from '@/types'

const vpn = (over: Partial<VpnProfile> = {}): VpnProfile => ({
  id: 1, name: 'Mullvad SE', mode: 'provider', provider: 'mullvad', provider_label: 'Mullvad',
  vpn_type: 'wireguard', has_config: false, config_filename: null,
  has_wireguard_private_key: true, has_wireguard_preshared_key: false,
  wireguard_addresses: '10.64.0.2/32', has_openvpn_user: false, has_openvpn_password: false,
  server_countries: 'Sweden', server_cities: null, server_hostnames: null,
  problem: null, used_by: [], active_in: null,
  ...over,
})
const ts = (over: Partial<TailscaleProfile> = {}): TailscaleProfile => ({
  id: 1, name: 'Home', has_auth_key: true, login_server: null, problem: null, used_by: [], ...over,
})

function setUser(provider: string) {
  useAuthStore().user = {
    id: 1, username: 'me', is_admin: false, auth_provider: provider,
  } as User
}

describe('PreferencesView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    setUser('local')
    vi.mocked(usersApi.listVpnProviders).mockResolvedValue([])
    vi.mocked(usersApi.listVpnProfiles).mockResolvedValue([])
    vi.mocked(usersApi.listTailscaleProfiles).mockResolvedValue([])
    vi.mocked(usersApi.getSshKey).mockResolvedValue({
      has_key: false, public_key: null, key_type: null, fingerprint: null,
    })
  })

  it('lists several VPN profiles with what each one is and its state', async () => {
    vi.mocked(usersApi.listVpnProfiles).mockResolvedValue([
      vpn({ active_in: 'Research', used_by: ['Research'] }),
      vpn({ id: 2, name: 'Mullvad CH', server_countries: 'Switzerland' }),
      vpn({
        id: 3, name: 'Office', mode: 'custom', provider: null, provider_label: null,
        vpn_type: 'openvpn', config_filename: 'office.ovpn', has_config: false,
        problem: 'no config file uploaded',
      }),
    ])
    const wrapper = mount(PreferencesView)
    await flushPromises()

    const rows = wrapper.findAll('#vpn .profile')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('Mullvad · WireGuard · Sweden')
    expect(rows[0].text()).toContain('Connected in Research')
    expect(rows[1].text()).toContain('Switzerland')
    expect(rows[1].text()).toContain('Ready')
    expect(rows[2].text()).toContain('OpenVPN · office.ovpn')
    expect(rows[2].text()).toContain("Can't connect: no config file uploaded")
  })

  it('shows an empty state per section', async () => {
    const wrapper = mount(PreferencesView)
    await flushPromises()
    expect(wrapper.find('#vpn').text()).toContain('No VPN profiles yet')
    expect(wrapper.find('#tailscale').text()).toContain('No Tailscale profiles yet')
  })

  it("won't offer Delete for a profile a workspace still uses", async () => {
    vi.mocked(usersApi.listTailscaleProfiles).mockResolvedValue([
      ts({ used_by: ['dev-box'] }),
      ts({ id: 2, name: 'Work', login_server: 'https://hs.example.com' }),
    ])
    const wrapper = mount(PreferencesView, { attachTo: document.body })
    await flushPromises()
    const rows = wrapper.findAll('#tailscale .profile')
    expect(rows[0].text()).toContain('used by dev-box')
    expect(rows[1].text()).toContain('hs.example.com')

    await rows[0].get('.row-menu-trigger').trigger('click')
    await flushPromises()
    const del = [...document.querySelectorAll<HTMLButtonElement>('.row-menu-item')]
      .find(b => b.textContent?.includes('Delete'))!
    expect(del.disabled).toBe(true)
    expect(del.title).toContain('dev-box')
    wrapper.unmount()
  })

  it('rejects a too-short new password without calling the API', async () => {
    const wrapper = mount(PreferencesView)
    await flushPromises()

    const pwInputs = wrapper.findAll('input[type="password"]')
    await pwInputs[0].setValue('oldpassword')
    await pwInputs[1].setValue('short')
    await pwInputs[2].setValue('short')

    await wrapper.findAll('form')[0].trigger('submit.prevent')
    await flushPromises()

    expect(authApi.changePassword).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('at least 8')
  })

  it('hides the change-password panel for SSO (OIDC) users', async () => {
    setUser('oidc')
    const wrapper = mount(PreferencesView)
    await flushPromises()
    expect(wrapper.text()).not.toContain('CHANGE PASSWORD')
    expect(wrapper.text()).toContain('TAILSCALE PROFILES')
    expect(wrapper.text()).toContain('VPN PROFILES')
  })
})
