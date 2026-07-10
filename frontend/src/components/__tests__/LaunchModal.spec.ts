import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { WorkspaceImage } from '@/types'

vi.mock('@/api/images', () => ({
  imagesApi: { list: vi.fn() },
}))

vi.mock('@/api/proot', () => ({
  prootApi: { list: vi.fn() },
}))

vi.mock('@/api/users', () => ({
  usersApi: { getGluetun: vi.fn().mockResolvedValue({ enabled: false, vpn_type: 'openvpn', has_config: false, config_filename: null, has_wireguard_private_key: false, has_openvpn_user: false, has_openvpn_password: false }) },
}))

vi.mock('@/api/workspaces', () => ({
  workspacesApi: { lanPolicy: vi.fn(), gpuPolicy: vi.fn(), dockerPolicy: vi.fn() },
}))

const launchMock = vi.fn()
vi.mock('@/stores/workspaces', () => ({
  useWorkspacesStore: () => ({ launch: launchMock }),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

// BaseModal just renders its default slot when open.
vi.mock('@/components/BaseModal.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

import { imagesApi } from '@/api/images'
import { prootApi } from '@/api/proot'
import { workspacesApi } from '@/api/workspaces'
import LaunchModal from '@/components/LaunchModal.vue'

const desktopImage: WorkspaceImage = {
  id: 7,
  name: 'Ubuntu Desktop',
  docker_image: 'ubuntu:desktop',
  image_type: 'desktop',
  description: null,
  internal_port: 6901,
  url_env: null,
  enabled: true,
  logo_url: null,
  created_at: '2026-01-01T00:00:00Z',
}

describe('LaunchModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    vi.mocked(imagesApi.list).mockResolvedValue([desktopImage])
    vi.mocked(prootApi.list).mockResolvedValue({ apps: ['firefox', 'obs-studio', 'blender'] })
    vi.mocked(workspacesApi.lanPolicy).mockResolvedValue({ enabled: false, subnets: [] })
    vi.mocked(workspacesApi.gpuPolicy).mockResolvedValue({ enabled: false })
    vi.mocked(workspacesApi.dockerPolicy).mockResolvedValue({ enabled: false })
    launchMock.mockResolvedValue({ id: 99 })
  })

  it('omits the ts_* fields when Tailscale routing is off', async () => {
    const wrapper = mount(LaunchModal, { props: { modelValue: true } })
    await flushPromises()

    await wrapper.find('input').setValue('My Desktop')
    await wrapper.find('select').setValue(7)
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(launchMock).toHaveBeenCalledOnce()
    const payload = launchMock.mock.calls[0][0]
    expect(payload.use_tailscale).toBe(false)
    expect(payload).not.toHaveProperty('ts_exit_node')
    expect(payload).not.toHaveProperty('ts_accept_routes')
    expect(payload).not.toHaveProperty('ts_accept_dns')
  })

  it('includes ts_exit_node / ts_accept_routes / ts_accept_dns when Tailscale is enabled', async () => {
    const wrapper = mount(LaunchModal, { props: { modelValue: true } })
    await flushPromises()

    await wrapper.find('input').setValue('My Desktop')
    await wrapper.find('select').setValue(7)

    // Enable Tailscale routing, which reveals the extra fields.
    const tsToggle = wrapper.find('input[type="checkbox"]')
    await tsToggle.setValue(true)

    const exitNode = wrapper.find('input[type="text"]')
    await exitNode.setValue('us-nyc-1')

    // Untick "Accept DNS" (last revealed Tailscale checkbox).
    const tsCheckboxes = wrapper.findAll('.ts-field input[type="checkbox"]')
    await tsCheckboxes[tsCheckboxes.length - 1].setValue(false)

    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const payload = launchMock.mock.calls[0][0]
    expect(payload.use_tailscale).toBe(true)
    expect(payload.ts_exit_node).toBe('us-nyc-1')
    expect(payload.ts_accept_routes).toBe(true)
    expect(payload.ts_accept_dns).toBe(false)
  })

  it('sends allow_sudo=false by default and omits package fields when blank', async () => {
    const wrapper = mount(LaunchModal, { props: { modelValue: true } })
    await flushPromises()

    await wrapper.find('input').setValue('My Desktop')
    await wrapper.find('select').setValue(7)
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const payload = launchMock.mock.calls[0][0]
    expect(payload.allow_sudo).toBe(false)
    expect(payload).not.toHaveProperty('install_packages')
    expect(payload).not.toHaveProperty('proot_apps')
  })

  it('includes install_packages (trimmed) and the selected proot_apps', async () => {
    const wrapper = mount(LaunchModal, { props: { modelValue: true } })
    await flushPromises()

    await wrapper.find('input').setValue('My Desktop')
    await wrapper.find('select').setValue(7)

    await wrapper.find('input[placeholder="git vim htop"]').setValue('  git vim htop  ')

    // proot-apps is now a multi-select: tick firefox (0) and blender (2).
    const boxes = wrapper.findAll('.proot-item input[type="checkbox"]')
    expect(boxes).toHaveLength(3)
    await boxes[0].setValue(true)
    await boxes[2].setValue(true)

    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const payload = launchMock.mock.calls[0][0]
    expect(payload.install_packages).toBe('git vim htop')
    expect(payload.proot_apps).toBe('firefox blender')
  })

  it('hides the LAN checkbox when the admin policy is disabled', async () => {
    const wrapper = mount(LaunchModal, { props: { modelValue: true } })
    await flushPromises()
    const lanRow = wrapper.findAll('.checkbox-row').find(r => r.text().includes('direct LAN'))
    expect(lanRow).toBeUndefined()
  })

  it('includes lan_access when the admin enables it and the box is ticked', async () => {
    vi.mocked(workspacesApi.lanPolicy).mockResolvedValue({ enabled: true, subnets: ['10.12.0.0/24'] })
    const wrapper = mount(LaunchModal, { props: { modelValue: true } })
    await flushPromises()

    await wrapper.find('input').setValue('My Desktop')
    await wrapper.find('select').setValue(7)

    const lanRow = wrapper.findAll('.checkbox-row').find(r => r.text().includes('Allow direct LAN access'))
    expect(lanRow).toBeTruthy()
    await lanRow!.find('input[type="checkbox"]').setValue(true)

    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(launchMock.mock.calls[0][0].lan_access).toBe(true)
  })
})
