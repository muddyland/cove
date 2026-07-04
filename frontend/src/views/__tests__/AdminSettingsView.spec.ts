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
  workspace_gpu_accel: false,
  workspace_gpu_render_node: '/dev/dri/renderD128',
  workspace_gpu_render_gid: 992,
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
      workspace_gpu_accel: false,
      workspace_gpu_render_node: '/dev/dri/renderD128',
      workspace_gpu_render_gid: 992,
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
    expect(text).toContain('// ENVIRONMENT')
    expect(text).toContain('COVE_BASE_DOMAIN')
    expect(text).toContain('example.com')
    expect(text).toContain('COVE_SUBDOMAIN_MODE')
    expect(text).toContain('true')

    const rows = wrapper.findAll('.env-table tbody tr')
    expect(rows).toHaveLength(2)
  })
})
