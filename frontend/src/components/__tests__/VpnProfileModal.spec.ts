import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/users', () => ({
  usersApi: { createVpnProfile: vi.fn(), updateVpnProfile: vi.fn() },
}))

import { usersApi } from '@/api/users'
import VpnProfileModal from '@/components/VpnProfileModal.vue'
import type { VpnProfile, VpnProvider } from '@/types'

const PROVIDERS: VpnProvider[] = [
  { value: 'mullvad', label: 'Mullvad', types: ['wireguard'], wg_addresses: true, wg_preshared_key: false },
  { value: 'nordvpn', label: 'NordVPN', types: ['wireguard', 'openvpn'], wg_addresses: false, wg_preshared_key: false },
]

const stored: VpnProfile = {
  id: 7, name: 'Mullvad SE', mode: 'provider', provider: 'mullvad', provider_label: 'Mullvad',
  vpn_type: 'wireguard', has_config: false, config_filename: null,
  has_wireguard_private_key: true, has_wireguard_preshared_key: false,
  wireguard_addresses: '10.64.0.2/32', has_openvpn_user: false, has_openvpn_password: false,
  server_countries: 'Sweden', server_cities: null, server_hostnames: null,
  problem: null, used_by: [], active_in: null,
}

function mountModal(profile: VpnProfile | null) {
  return mount(VpnProfileModal, {
    props: { modelValue: true, profile, providers: PROVIDERS },
    attachTo: document.body,
  })
}
const q = <T extends Element>(sel: string) => document.body.querySelector<T>(sel)!
async function type(sel: string, value: string) {
  const el = q<HTMLInputElement | HTMLSelectElement>(sel)
  el.value = value
  el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input'))
  await flushPromises()
}

describe('VpnProfileModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    vi.mocked(usersApi.createVpnProfile).mockResolvedValue(stored)
    vi.mocked(usersApi.updateVpnProfile).mockResolvedValue(stored)
  })

  it('creates a provider profile with key, address and server filters', async () => {
    const w = mountModal(null)
    await flushPromises()
    await type('#vpn-name', 'Mullvad CH')
    await type('#vpn-provider', 'mullvad')
    // Mullvad is WireGuard-only, so the protocol is fixed.
    expect(q<HTMLSelectElement>('#vpn-type').disabled).toBe(true)
    await type('#vpn-wgkey', 'KEY-B')
    await type('#vpn-wgaddr', '10.64.0.3/32')
    await type('#vpn-countries', 'Switzerland')
    q<HTMLFormElement>('form').dispatchEvent(new Event('submit'))
    await flushPromises()

    expect(usersApi.createVpnProfile).toHaveBeenCalledOnce()
    const payload = vi.mocked(usersApi.createVpnProfile).mock.calls[0][0]
    expect(payload).toMatchObject({
      name: 'Mullvad CH', mode: 'provider', provider: 'mullvad', vpn_type: 'wireguard',
      wireguard_private_key: 'KEY-B', wireguard_addresses: '10.64.0.3/32', server_countries: 'Switzerland',
    })
    expect(payload).not.toHaveProperty('config_file')
    expect(w.emitted('saved')).toHaveLength(1)
    w.unmount()
  })

  it('keeps stored secrets unless replaced or cleared', async () => {
    const w = mountModal(stored)
    await flushPromises()
    expect(q<HTMLInputElement>('#vpn-wgkey').placeholder).toContain('stored')
    q<HTMLFormElement>('form').dispatchEvent(new Event('submit'))
    await flushPromises()
    let payload = vi.mocked(usersApi.updateVpnProfile).mock.calls[0][1]
    expect(payload).not.toHaveProperty('wireguard_private_key')
    w.unmount()

    // Clear -> sent as '' (the API clears it).
    const w2 = mountModal(stored)
    await flushPromises()
    q<HTMLButtonElement>('.secret .clear').click()
    await flushPromises()
    q<HTMLFormElement>('form').dispatchEvent(new Event('submit'))
    await flushPromises()
    payload = vi.mocked(usersApi.updateVpnProfile).mock.calls[1][1]
    expect(payload.wireguard_private_key).toBe('')
    w2.unmount()
  })

  it('needs a file before saving a new config-file profile', async () => {
    const w = mountModal(null)
    await flushPromises()
    await type('#vpn-name', 'Office')
    const fileBtn = [...document.body.querySelectorAll<HTMLButtonElement>('.segmented button')]
      .find(b => b.textContent?.includes('Config file'))!
    fileBtn.click()
    await flushPromises()
    q<HTMLFormElement>('form').dispatchEvent(new Event('submit'))
    await flushPromises()
    expect(usersApi.createVpnProfile).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Choose a config file')
    w.unmount()
  })
})
