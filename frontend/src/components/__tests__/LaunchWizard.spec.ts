import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import type { WorkspaceImage } from '@/types'

vi.mock('@/api/images', () => ({ imagesApi: { list: vi.fn() } }))
vi.mock('@/api/proot', () => ({ prootApi: { list: vi.fn() } }))
vi.mock('@/api/users', () => ({
  usersApi: {
    listVpnProfiles: vi.fn().mockResolvedValue([
      { id: 11, name: 'Key A', mode: 'provider', provider: 'mullvad', provider_label: 'Mullvad',
        vpn_type: 'wireguard', server_countries: 'Sweden', problem: null, used_by: ['busy'], active_in: 'busy' },
      { id: 12, name: 'Key B', mode: 'provider', provider: 'mullvad', provider_label: 'Mullvad',
        vpn_type: 'wireguard', server_countries: 'Sweden', problem: null, used_by: [], active_in: null },
      { id: 13, name: 'Broken', mode: 'custom', provider: null, provider_label: null,
        vpn_type: 'openvpn', config_filename: null, problem: 'no config file uploaded', used_by: [], active_in: null },
    ]),
    listTailscaleProfiles: vi.fn().mockResolvedValue([
      { id: 5, name: 'Home', has_auth_key: true, login_server: null, problem: null, used_by: [] },
    ]),
  },
}))
vi.mock('@/api/workspaces', () => ({
  workspacesApi: { lanPolicy: vi.fn(), gpuPolicy: vi.fn(), dockerPolicy: vi.fn() },
}))

const launchMock = vi.fn()
vi.mock('@/stores/workspaces', () => ({ useWorkspacesStore: () => ({ launch: launchMock }) }))
// The zones this user may use: Local only by default; tests change it.
const zonesState = vi.hoisted(() => ({
  items: [{ id: 0, name: 'Local' }] as { id: number; name: string }[],
  hasRemote: false,
  loaded: true,
  fetch: async () => [] as unknown[],
}))
vi.mock('@/stores/zones', () => ({ useZonesStore: () => zonesState }))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ toast: vi.fn() }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/components/BaseModal.vue', () => ({ default: { template: '<div><slot /></div>' } }))

import { imagesApi } from '@/api/images'
import { prootApi } from '@/api/proot'
import { workspacesApi } from '@/api/workspaces'
import LaunchWizard from '@/components/LaunchWizard.vue'

const NO_BROWSER = { kiosk: false, fullscreen: false, dark: false }
const CHROMIUM = { kiosk: true, fullscreen: true, dark: true }

const desktopImage: WorkspaceImage = {
  id: 7, name: 'Ubuntu Desktop', docker_image: 'ubuntu:desktop', image_type: 'desktop',
  description: null, internal_port: 3000, url_env: null, enabled: true, logo_url: null,
  created_at: '2026-01-01T00:00:00Z', browser: NO_BROWSER,
}

function clickBtn(wrapper: VueWrapper, text: string) {
  const btn = wrapper.findAll('button').find(b => b.text().trim().startsWith(text))
  if (!btn) throw new Error(`button "${text}" not found`)
  return btn.trigger('click')
}

// Choose what to launch, pick the (only) image, and advance to the Basics step.
async function toBasics(wrapper: VueWrapper, kind = 'Desktop') {
  await flushPromises()
  const kindCard = wrapper.findAll('.kind-card').find(c => c.text().includes(kind))!
  await kindCard.trigger('click')
  await wrapper.find('.card').trigger('click') // selects the image, prefills name
  await clickBtn(wrapper, 'Next')
}

describe('LaunchWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    zonesState.items = [{ id: 0, name: 'Local' }]
    zonesState.hasRemote = false
    zonesState.fetch = async () => zonesState.items
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

  it('launches on the only zone a zone-limited user may use', async () => {
    zonesState.items = [{ id: 3, name: 'LAN' }]
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    await clickBtn(wrapper, 'Launch')
    await flushPromises()
    expect(launchMock.mock.calls[0][0].zone_id).toBe(3)
  })

  it('explains and blocks a launch when the user may use no zone', async () => {
    zonesState.items = []
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    expect(wrapper.text()).toContain("isn't allowed to run workspaces on any zone")
    // No "Ready to launch" next to that, and Launch looks as blocked as it is.
    expect(wrapper.text()).not.toContain('Ready to launch')
    const launch = wrapper.findAll('button').find(b => b.text().trim().startsWith('Launch'))!
    expect(launch.attributes('disabled')).toBeDefined()
    await clickBtn(wrapper, 'Launch')
    await flushPromises()
    expect(launchMock).not.toHaveBeenCalled()
  })

  it('includes ts_* fields when Tailscale is chosen in the Network step', async () => {
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    await clickBtn(wrapper, 'Customize') // -> Network step

    // Segmented control: pick Tailscale (enabled because getTailscale.has_auth_key).
    const tsSeg = wrapper.findAll('.segmented button').find(b => b.text().includes('Tailscale'))!
    await tsSeg.trigger('click')

    await wrapper.find('.ts-field input[type="text"]').setValue('us-nyc-1')
    const acceptDns = wrapper.findAll('.ts-field [role="switch"]').find(t => t.text().includes('Accept DNS'))!
    await acceptDns.trigger('click') // turn Accept DNS off

    await clickBtn(wrapper, 'Launch')
    await flushPromises()

    const payload = launchMock.mock.calls[0][0]
    expect(payload.use_tailscale).toBe(true)
    expect(payload.tailscale_profile_id).toBe(5)
    expect(payload.ts_exit_node).toBe('us-nyc-1')
    expect(payload.ts_accept_routes).toBe(true)
    expect(payload.ts_accept_dns).toBe(false)
  })

  it('picks a VPN profile that is free and sends its id', async () => {
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper)
    await clickBtn(wrapper, 'Customize')
    const vpnSeg = wrapper.findAll('.segmented button').find(b => b.text().includes('Gluetun'))!
    await vpnSeg.trigger('click')

    const select = wrapper.get('#net-vpn-profile')
    // Key A is connected elsewhere, so the free Key B is pre-selected.
    expect((select.element as HTMLSelectElement).value).toBe('12')
    const opts = select.findAll('option')
    expect(opts[0].text()).toContain('connected in busy')
    expect(opts[2].attributes('disabled')).toBeDefined()
    expect(opts[2].text()).toContain('no config file uploaded')

    // Choosing the busy one warns before launch.
    await select.setValue(11)
    expect(wrapper.text()).toContain('can\'t start until that one stops')

    await select.setValue(12)
    await clickBtn(wrapper, 'Launch')
    await flushPromises()
    const payload = launchMock.mock.calls[0][0]
    expect(payload.use_gluetun).toBe(true)
    expect(payload.gluetun_profile_id).toBe(12)
    expect(payload).not.toHaveProperty('tailscale_profile_id')
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
    const dnsToggle = wrapper.findAll('[role="switch"]').find(r => r.text().includes('custom DNS'))!
    await dnsToggle.trigger('click')
    await wrapper.find('input[placeholder="1.1.1.1 9.9.9.9"]').setValue('not-an-ip')

    await clickBtn(wrapper, 'Launch')
    await flushPromises()

    expect(launchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Not a valid IP')
  })

  it('hides the Apps step for non-desktop (browser) images', async () => {
    vi.mocked(imagesApi.list).mockResolvedValue([
      { ...desktopImage, id: 8, name: 'Brave', image_type: 'browser', url_env: 'BRAVE_CLI', browser: CHROMIUM },
    ])
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper, 'Browser')
    await clickBtn(wrapper, 'Customize')    // Network
    await clickBtn(wrapper, 'Next: Access') // Access
    // For a browser there is no "Next: Apps" — Access goes straight to Review.
    const appsBtn = wrapper.findAll('button').find(b => b.text().includes('Next: Apps'))
    expect(appsBtn).toBeUndefined()
    expect(wrapper.findAll('button').some(b => b.text().startsWith('Review'))).toBe(true)
  })

  it('asks what to launch first and only offers images of that kind', async () => {
    vi.mocked(imagesApi.list).mockResolvedValue([
      desktopImage,
      { ...desktopImage, id: 8, name: 'Brave', image_type: 'browser', url_env: 'BRAVE_CLI', browser: CHROMIUM },
      { ...desktopImage, id: 9, name: 'VSCodium', image_type: 'app' },
    ])
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await flushPromises()
    // Three choices, before any image is shown.
    expect(wrapper.findAll('.kind-card')).toHaveLength(3)
    expect(wrapper.findAll('.card')).toHaveLength(0)

    await wrapper.findAll('.kind-card').find(c => c.text().includes('Browser'))!.trigger('click')
    const shown = wrapper.findAll('.card').map(c => c.text())
    expect(shown).toHaveLength(1)
    expect(shown[0]).toContain('Brave')
  })

  it('offers dark mode without kiosk, defaulting to this browser\'s setting', async () => {
    const brave = { ...desktopImage, id: 12, name: 'Brave', image_type: 'browser' as const, url_env: 'BRAVE_CLI', browser: CHROMIUM }
    vi.mocked(imagesApi.list).mockResolvedValue([brave])
    // The launcher follows the browser: dark here, so dark there.
    const mql = window.matchMedia
    window.matchMedia = ((q: string) => ({ matches: q.includes('dark'), media: q, addEventListener() {}, removeEventListener() {} })) as any

    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper, 'Browser')
    const dark = wrapper.findAll('[role="switch"]').find(t => t.text().includes('Dark mode'))!
    expect(dark.attributes('aria-checked')).toBe('true')  // on, without touching kiosk
    expect(wrapper.findAll('[role="switch"]').find(t => t.text().includes('Kiosk'))!.attributes('aria-checked')).toBe('false')

    await wrapper.find('textarea').setValue('https://example.com')
    await clickBtn(wrapper, 'Launch')
    await flushPromises()
    const payload = launchMock.mock.calls[0][0]
    expect(payload.kiosk).toBe(false)
    expect(payload.kiosk_dark).toBe(true)  // dark without kiosk reaches the server
    window.matchMedia = mql
  })

  it('leaves dark mode off when this browser is light', async () => {
    const brave = { ...desktopImage, id: 12, name: 'Brave', image_type: 'browser' as const, url_env: 'BRAVE_CLI', browser: CHROMIUM }
    vi.mocked(imagesApi.list).mockResolvedValue([brave])
    const mql = window.matchMedia
    window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} })) as any
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper, 'Browser')
    const dark = wrapper.findAll('[role="switch"]').find(t => t.text().includes('Dark mode'))!
    expect(dark.attributes('aria-checked')).toBe('false')
    window.matchMedia = mql
  })

  it('only offers the start-up options a browser supports', async () => {
    const vivaldi = { ...desktopImage, id: 10, name: 'Vivaldi', image_type: 'browser' as const, url_env: 'VIVALDI_CLI', browser: NO_BROWSER }
    const firefox = { ...desktopImage, id: 11, name: 'Firefox', image_type: 'browser' as const, url_env: 'FIREFOX_CLI', browser: { kiosk: true, fullscreen: false, dark: false } }
    const brave = { ...desktopImage, id: 12, name: 'Brave', image_type: 'browser' as const, url_env: 'BRAVE_CLI', browser: CHROMIUM }

    // Chromium-family: kiosk, and inside it dark + the menu variant.
    vi.mocked(imagesApi.list).mockResolvedValue([brave])
    let wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper, 'Browser')
    expect(wrapper.text()).toContain('Kiosk mode')
    expect(wrapper.text()).toContain('Dark mode')  // independent of the kiosk toggle
    await wrapper.findAll('[role="switch"]').find(t => t.text().includes('Kiosk'))!.trigger('click')
    expect(wrapper.text()).toContain('Allow right-click')

    // Firefox has --kiosk but no full-screen or dark switch.
    vi.mocked(imagesApi.list).mockResolvedValue([firefox])
    wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper, 'Browser')
    expect(wrapper.text()).toContain('Kiosk mode')
    expect(wrapper.text()).not.toContain('Dark mode')  // Firefox has no dark switch
    await wrapper.findAll('[role="switch"]').find(t => t.text().includes('Kiosk'))!.trigger('click')
    expect(wrapper.text()).not.toContain('Allow right-click')

    // Vivaldi honours none of them, so nothing is offered.
    vi.mocked(imagesApi.list).mockResolvedValue([vivaldi])
    wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await toBasics(wrapper, 'Browser')
    expect(wrapper.text()).not.toContain('Kiosk mode')
    expect(wrapper.text()).not.toContain('Start full-screen')
    expect(wrapper.text()).toContain('ignores start-up window flags')
  })
})
describe('LaunchWizard: GPU and Docker per zone', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    zonesState.items = [{ id: 0, name: 'Local' }]
    zonesState.hasRemote = false
    zonesState.fetch = async () => zonesState.items
    vi.mocked(imagesApi.list).mockResolvedValue([desktopImage])
    vi.mocked(prootApi.list).mockResolvedValue({ apps: [] })
    vi.mocked(workspacesApi.lanPolicy).mockResolvedValue({ enabled: false, subnets: [] })
    // Local offers both; zone 3 offers neither.
    vi.mocked(workspacesApi.gpuPolicy).mockImplementation(async (z = 0) => ({ enabled: z === 0 }))
    vi.mocked(workspacesApi.dockerPolicy).mockImplementation(async (z = 0) => ({ enabled: z === 0 }))
    launchMock.mockResolvedValue({ id: 99 })
  })

  it('re-reads the policies for the chosen zone and drops choices it lacks', async () => {
    const wrapper = mount(LaunchWizard, { props: { modelValue: true } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      form: { zone_id: number; use_docker: boolean; gpu_accel: boolean }
      dockerPolicy: { enabled: boolean }
    }
    expect(vm.dockerPolicy.enabled).toBe(true)
    vm.form.use_docker = true
    vm.form.gpu_accel = true

    vm.form.zone_id = 3
    await flushPromises()
    expect(workspacesApi.dockerPolicy).toHaveBeenLastCalledWith(3)
    expect(workspacesApi.gpuPolicy).toHaveBeenLastCalledWith(3)
    expect(vm.dockerPolicy.enabled).toBe(false)
    expect(vm.form.use_docker).toBe(false)
    expect(vm.form.gpu_accel).toBe(false)
  })
})
