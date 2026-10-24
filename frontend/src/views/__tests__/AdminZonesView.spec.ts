import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { Zone } from '@/types'

const remote: Zone = {
  id: 2, public_id: 'z2', name: 'LAN', status: 'enrolled', endpoint_host: '10.0.0.5',
  endpoint_port: 8443, enrolled_at: null, last_seen_at: null, created_at: '2026-01-01T00:00:00Z',
  workspace_count: 2, gpu_accel: null, gpu_render_node: null, gpu_render_gid: null,
  docker_enabled: false, agent_allows_docker: false,
}

vi.mock('@/api/zones', () => ({
  zonesApi: { list: vi.fn(), health: vi.fn(), update: vi.fn() },
}))
vi.mock('@/api/admin', () => ({
  adminApi: { settings: { get: vi.fn() } },
}))
const toastMock = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ toast: toastMock }) }))
vi.mock('@/components/AppShell.vue', () => ({ default: { template: '<div><slot /></div>' } }))
vi.mock('@/components/BaseModal.vue', () => ({
  default: { props: ['modelValue'], template: '<div v-if="modelValue"><slot /></div>' },
}))

import { zonesApi } from '@/api/zones'
import { adminApi } from '@/api/admin'
import AdminZonesView from '@/views/AdminZonesView.vue'

function clickBtn(wrapper: ReturnType<typeof mount>, text: string) {
  const btn = wrapper.findAll('button').find(b => b.text().trim().startsWith(text))
  if (!btn) throw new Error(`button "${text}" not found`)
  return btn.trigger('click')
}

describe('AdminZonesView zone settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    vi.mocked(zonesApi.list).mockResolvedValue([remote])
    vi.mocked(zonesApi.health).mockResolvedValue({
      ok: true, latency_ms: 3, docker_version: '29', containers_running: 1, error: null, agent_dind: false,
    })
    vi.mocked(zonesApi.update).mockResolvedValue(remote)
    vi.mocked(adminApi.settings.get).mockResolvedValue({
      workspace_gpu_accel: false, workspace_gpu_render_node: '/dev/dri/renderD128',
      workspace_gpu_render_gid: 992, workspace_docker: true,
    } as never)
  })

  it('saves GPU overrides and the DinD opt-in, sending null for inherit', async () => {
    const wrapper = mount(AdminZonesView)
    await flushPromises()
    await clickBtn(wrapper, 'Settings')
    await flushPromises()

    await wrapper.find('select').setValue('on')
    const inputs = wrapper.findAll('form input')
    await inputs[0].setValue('/dev/dri/renderD129')
    // GID left empty -> inherit.
    await wrapper.findAll('[role="switch"]')[0].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('does not allow Docker-in-Docker yet')

    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(zonesApi.update).toHaveBeenCalledWith(2, {
      gpu_accel: true,
      gpu_render_node: '/dev/dri/renderD129',
      gpu_render_gid: null,
      docker_enabled: true,
    })
  })

  it('keeps the DinD toggle disabled while the master toggle is off', async () => {
    vi.mocked(adminApi.settings.get).mockResolvedValue({ workspace_docker: false } as never)
    const wrapper = mount(AdminZonesView)
    await flushPromises()
    await clickBtn(wrapper, 'Settings')
    await flushPromises()
    const sw = wrapper.findAll('[role="switch"]')[0]
    expect(sw.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('master toggle')
  })
})
