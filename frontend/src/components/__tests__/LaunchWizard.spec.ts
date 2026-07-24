import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import type { WorkspaceImage } from '@/types'

vi.mock('@/api/images', () => ({ imagesApi: { list: vi.fn() } }))
vi.mock('@/api/proot', () => ({ prootApi: { list: vi.fn() } }))
vi.mock('@/api/users', () => ({
  usersApi: {
    getGluetun: vi.fn().mockResolvedValue({ enabled: false, has_config: false }),
    getTailscale: vi.fn().mockResolvedValue({ has_auth_key: true }),
  },
}))
vi.mock('@/api/workspaces', () => ({
  workspacesApi: { lanPolicy: vi.fn(), gpuPolicy: vi.fn(), dockerPolicy: vi.fn() },
}))

const launchMock = vi.fn()
vi.mock('@/stores/workspaces', () => ({ useWorkspacesStore: () => ({ launch: launchMock }) }))
vi.mock('@/stores/zones', () => ({ useZonesStore: () => ({ fetch: vi.fn(), hasRemote: false, items: [] }) }))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ toast: vi.fn() }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/components/BaseModal.vue', () => ({ default: { template: '<div><slot /></div>' } }))

import { imagesApi } from '@/api/images'
import { prootApi } from '@/api/proot'
import { workspacesApi } from '@/api/workspaces'
import LaunchWizard from '@/components/LaunchWizard.vue'

const desktopImage: WorkspaceImage = {
  id: 7, name: 'Ubuntu Desktop', docker_image: 'ubuntu:desktop', image_type: 'desktop',
  description: null, internal_port: 3000, url_env: null, enabled: true, logo_url: null,
  created_at: '2026-01-01T00:00:00Z',
}

function clickBtn(wrapper: VueWrapper, text: string) {
  const btn = wrapper.findAll('button').find(b => b.text().trim().startsWith(text))
  if (!btn) throw new Error(`button "${text}" not found`)
  return btn.trigger('click')
}

// Pick the (only) image and advance to the Basics step.
async function toBasics(wrapper: VueWrapper) {
  await flushPromises()
  await wrapper.find('.card').trigger('click') // selects image 7, prefills name
  await clickBtn(wrapper, 'Next')
}

describe('LaunchWizard', () => {
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

  it('launches with the minimal payload (name + image) and omits ts_* when Direct', async () => {
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    await clickBtn(wrapper, 'Launch')
    await flushPromises()

    expect(launchMock).toHaveBeenCalledOnce()
    const payload = launchMock.mock.calls[0][0]
    expect(payload.name).toBe('Ubuntu Desktop')
    expect(payload.image_id).toBe(7)
    expect(payload.workspace_type).toBe('desktop')
    expect(payload.use_tailscale).toBe(false)
    expect(payload).not.toHaveProperty('ts_exit_node')
  })

  it('includes ts_* fields when Tailscale is chosen in the Network step', async () => {
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    await clickBtn(wrapper, 'Customize') // -> Network step

    // Segmented control: pick Tailscale (enabled because getTailscale.has_auth_key).
    const tsSeg = wrapper.findAll('.segmented button').find(b => b.text().includes('Tailscale'))!
    await tsSeg.trigger('click')

    await wrapper.find('.ts-field input[type="text"]').setValue('us-nyc-1')
    const tsChecks = wrapper.findAll('.ts-field input[type="checkbox"]')
    await tsChecks[tsChecks.length - 1].setValue(false) // untick Accept DNS

    await clickBtn(wrapper, 'Launch')
    await flushPromises()

    const payload = launchMock.mock.calls[0][0]
    expect(payload.use_tailscale).toBe(true)
    expect(payload.ts_exit_node).toBe('us-nyc-1')
    expect(payload.ts_accept_routes).toBe(true)
    expect(payload.ts_accept_dns).toBe(false)
  })

  it('includes install_packages (trimmed) and selected proot_apps from the Apps step', async () => {
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    await clickBtn(wrapper, 'Customize')      // Network
    await clickBtn(wrapper, 'Next: Access')   // Access
    await clickBtn(wrapper, 'Next: Apps')     // Apps (desktop only)

    await wrapper.find('input[placeholder="git vim htop"]').setValue('  git vim htop  ')
    const boxes = wrapper.findAll('.proot-item input[type="checkbox"]')
    expect(boxes).toHaveLength(3)
    await boxes[0].setValue(true) // firefox
    await boxes[2].setValue(true) // blender

    await clickBtn(wrapper, 'Launch')
    await flushPromises()

    const payload = launchMock.mock.calls[0][0]
    expect(payload.install_packages).toBe('git vim htop')
    expect(payload.proot_apps).toBe('firefox blender')
  })

  it('blocks Launch on an invalid custom DNS entry', async () => {
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    await clickBtn(wrapper, 'Customize') // Network (Direct by default)

    // Enable custom DNS and enter a bad value.
    const dnsToggle = wrapper.findAll('.checkbox-row').find(r => r.text().includes('custom DNS'))!
    await dnsToggle.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('input[placeholder="1.1.1.1 9.9.9.9"]').setValue('not-an-ip')

    await clickBtn(wrapper, 'Launch')
    await flushPromises()

    expect(launchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Not a valid IP')
  })

  it('hides the Apps step for non-desktop (browser) images', async () => {
    vi.mocked(imagesApi.list).mockResolvedValue([
      { ...desktopImage, id: 8, name: 'Brave', image_type: 'browser', url_env: 'BRAVE_CLI' },
    ])
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    await clickBtn(wrapper, 'Customize')    // Network
    await clickBtn(wrapper, 'Next: Access') // Access
    // For a browser there is no "Next: Apps" — Access goes straight to Review.
    const appsBtn = wrapper.findAll('button').find(b => b.text().includes('Next: Apps'))
    expect(appsBtn).toBeUndefined()
    expect(wrapper.findAll('button').some(b => b.text().startsWith('Review'))).toBe(true)
  })
})
