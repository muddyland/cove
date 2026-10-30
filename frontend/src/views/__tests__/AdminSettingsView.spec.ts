import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/admin', () => ({
  adminApi: {
    settings: {
      get: vi.fn(),
      update: vi.fn(),
    },
    env: {
      get: vi.fn(),
    },
    networkCheck: vi.fn().mockResolvedValue({
      client_ip: '203.0.113.9', is_lan: false, forwarded_for: ['203.0.113.9'], claimed: [], warning: null,
    }),
  },
}))

// AppShell pulls in router/stores we don't need here — stub it to a passthrough.
vi.mock('@/components/AppShell.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

import { adminApi } from '@/api/admin'
import AdminSettingsView from '@/views/AdminSettingsView.vue'
import type { AppSettings } from '@/types'

const baseSettings: AppSettings = {
  tailscale_image: 'tailscale/tailscale:v1.74.0',
  gluetun_image: 'qmcgaw/gluetun:latest',
  workspace_lan_access: false,
  workspace_lan_subnets: '',
  workspace_no_new_privileges: false,
  workspace_max_runtime_hours: 24,
  workspace_cpu_limit: 0,
  workspace_memory_limit_mb: 0,
  workspace_pids_limit: 8192,
  workspace_gpu_accel: false,
  workspace_gpu_render_node: '/dev/dri/renderD128',
  workspace_gpu_render_gid: 992,
  workspace_docker: false,
  dind_image: 'docker:dind',
  trash_retention_days: 30,
  default_max_workspaces: 0,
  default_max_running: 0,
  signin_lan_networks: '10.0.0.0/8, 192.168.0.0/16, fc00::/7, fe80::/10',
  trusted_proxies: '',
}

describe('AdminSettingsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    // Default the env summary so unrelated tests don't reject on mount.
    vi.mocked(adminApi.env.get).mockResolvedValue({ entries: [] })
  })

  it('loads settings on mount and populates fields', async () => {
    vi.mocked(adminApi.settings.get).mockResolvedValue(baseSettings)
    const wrapper = mount(AdminSettingsView)
    await flushPromises()

    expect(adminApi.settings.get).toHaveBeenCalledOnce()
    const imageInput = wrapper.find('input[type="text"]').element as HTMLInputElement
    expect(imageInput.value).toBe('tailscale/tailscale:v1.74.0')
    const checkbox = wrapper.find('input[type="checkbox"]').element as HTMLInputElement
    expect(checkbox.checked).toBe(false)
  })

  it('saves changed values', async () => {
    vi.mocked(adminApi.settings.get).mockResolvedValue(baseSettings)
    vi.mocked(adminApi.settings.update).mockResolvedValue({
      tailscale_image: 'tailscale/tailscale:latest',
      gluetun_image: 'qmcgaw/gluetun:latest',
      workspace_lan_access: true,
      workspace_lan_subnets: '',
      workspace_no_new_privileges: false,
      workspace_max_runtime_hours: 24,
      workspace_cpu_limit: 0,
      workspace_memory_limit_mb: 0,
      workspace_pids_limit: 8192,
      workspace_gpu_accel: false,
      workspace_gpu_render_node: '/dev/dri/renderD128',
      workspace_gpu_render_gid: 992,
      workspace_docker: false,
      dind_image: 'docker:dind',
      trash_retention_days: 30,
      default_max_workspaces: 0,
      default_max_running: 0,
      signin_lan_networks: '10.0.0.0/8, 192.168.0.0/16, fc00::/7, fe80::/10',
      trusted_proxies: '',
    })
    const wrapper = mount(AdminSettingsView)
    await flushPromises()

    await wrapper.find('input[type="text"]').setValue('tailscale/tailscale:latest')
    await wrapper.find('input[type="checkbox"]').setValue(true)

    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(adminApi.settings.update).toHaveBeenCalledOnce()
    const payload = vi.mocked(adminApi.settings.update).mock.calls[0][0]
    expect(payload.tailscale_image).toBe('tailscale/tailscale:latest')
    expect(payload.workspace_lan_access).toBe(true)
  })

  it('shows how Cove sees this connection next to the sign-in settings', async () => {
    vi.mocked(adminApi.settings.get).mockResolvedValue(baseSettings)
    vi.mocked(adminApi.networkCheck).mockResolvedValueOnce({
      client_ip: '10.9.0.2', is_lan: false, forwarded_for: ['10.9.0.2'], claimed: ['203.0.113.9'],
      warning: 'This request came through a tunnel or CDN.',
    })
    const wrapper = mount(AdminSettingsView)
    await flushPromises()
    const box = wrapper.find('.netcheck')
    expect(box.text()).toContain('10.9.0.2')
    expect(box.text()).toContain('outside the local network')
    expect(box.text()).toContain('tunnel or CDN')

    vi.mocked(adminApi.settings.update).mockResolvedValue({ ...baseSettings, trusted_proxies: '10.9.0.2/32' })
    const proxies = wrapper.findAll('input[type="text"]').find(i => i.attributes('placeholder') === 'none')!
    await proxies.setValue('10.9.0.2')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(vi.mocked(adminApi.settings.update).mock.calls[0][0].trusted_proxies).toBe('10.9.0.2')
    expect(adminApi.networkCheck).toHaveBeenCalledTimes(2) // re-checked with the saved settings
  })

  it('loads and renders the environment summary', async () => {
    vi.mocked(adminApi.settings.get).mockResolvedValue(baseSettings)
    vi.mocked(adminApi.env.get).mockResolvedValue({
      entries: [
        { name: 'COVE_BASE_DOMAIN', value: 'example.com' },
        { name: 'COVE_SUBDOMAIN_MODE', value: 'true' },
      ],
    })
    const wrapper = mount(AdminSettingsView)
    await flushPromises()

    expect(adminApi.env.get).toHaveBeenCalledOnce()
    const text = wrapper.text()
    expect(text).toContain('ENVIRONMENT')
    expect(text).toContain('COVE_BASE_DOMAIN')
    expect(text).toContain('example.com')
    expect(text).toContain('COVE_SUBDOMAIN_MODE')
    expect(text).toContain('true')

    const rows = wrapper.findAll('.env-table tbody tr')
    expect(rows).toHaveLength(2)
  })
})
