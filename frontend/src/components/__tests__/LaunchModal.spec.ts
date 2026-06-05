import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { WorkspaceImage } from '@/types'

vi.mock('@/api/images', () => ({
  imagesApi: { list: vi.fn() },
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

  it('always sends allow_sudo and omits package fields when blank', async () => {
    const wrapper = mount(LaunchModal, { props: { modelValue: true } })
    await flushPromises()

    await wrapper.find('input').setValue('My Desktop')
    await wrapper.find('select').setValue(7)
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const payload = launchMock.mock.calls[0][0]
    expect(payload.allow_sudo).toBe(true)
    expect(payload).not.toHaveProperty('install_packages')
    expect(payload).not.toHaveProperty('proot_apps')
  })

  it('includes install_packages / proot_apps (trimmed) and allow_sudo=false when set', async () => {
    const wrapper = mount(LaunchModal, { props: { modelValue: true } })
    await flushPromises()

    await wrapper.find('input').setValue('My Desktop')
    await wrapper.find('select').setValue(7)

    // The Advanced "Allow sudo" checkbox is the only non-Tailscale checkbox here.
    const sudoToggle = wrapper.find('.advanced input[type="checkbox"]')
    await sudoToggle.setValue(false)

    const advInputs = wrapper.findAll('.advanced input[type="text"]')
    await advInputs[0].setValue('  git vim htop  ')
    await advInputs[1].setValue('  firefox obs-studio  ')

    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const payload = launchMock.mock.calls[0][0]
    expect(payload.allow_sudo).toBe(false)
    expect(payload.install_packages).toBe('git vim htop')
    expect(payload.proot_apps).toBe('firefox obs-studio')
  })
})
