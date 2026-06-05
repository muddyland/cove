import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/admin', () => ({
  adminApi: {
    settings: {
      get: vi.fn(),
      update: vi.fn(),
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
  workspace_lan_access: false,
}

describe('AdminSettingsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
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
      workspace_lan_access: true,
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
})
