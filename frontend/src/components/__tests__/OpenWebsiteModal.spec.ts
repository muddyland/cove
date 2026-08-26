import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import type { WorkspaceImage } from '@/types'

vi.mock('@/api/images', () => ({ imagesApi: { list: vi.fn() } }))
vi.mock('@/api/users', () => ({
  usersApi: { getGluetun: vi.fn(), getTailscale: vi.fn() },
}))

const launchMock = vi.fn()
vi.mock('@/stores/workspaces', () => ({ useWorkspacesStore: () => ({ launch: launchMock }) }))
vi.mock('@/stores/zones', () => ({ useZonesStore: () => ({ fetch: vi.fn(), hasRemote: false, items: [] }) }))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ toast: vi.fn() }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/components/BaseModal.vue', () => ({ default: { template: '<div><slot /></div>' } }))

import { imagesApi } from '@/api/images'
import { usersApi } from '@/api/users'
import OpenWebsiteModal from '@/components/OpenWebsiteModal.vue'

function browser(id: number, name: string, logo: string | null = null): WorkspaceImage {
  return {
    id, name, docker_image: `lsio/${name.toLowerCase()}`, image_type: 'browser',
    description: null, internal_port: 3000, url_env: 'COVE_URL', enabled: true,
    logo_url: logo, created_at: '2026-01-01T00:00:00Z',
  }
}
const FIREFOX = browser(1, 'Firefox', 'https://logo/firefox.png')
const BRAVE = browser(2, 'Brave')

function toggleFor(wrapper: VueWrapper, text: string) {
  const t = wrapper.findAll('[role="switch"]').find(s => s.text().includes(text))
  if (!t) throw new Error(`toggle "${text}" not found`)
  return t
}
function segment(wrapper: VueWrapper, text: string) {
  const b = wrapper.findAll('.segmented button').find(s => s.text().includes(text))
  if (!b) throw new Error(`segment "${text}" not found`)
  return b
}

async function openModal(opts: { gluetun?: boolean; tailscale?: boolean } = {}) {
  vi.mocked(usersApi.getGluetun).mockResolvedValue({ enabled: !!opts.gluetun, has_config: !!opts.gluetun } as any)
  vi.mocked(usersApi.getTailscale).mockResolvedValue({ has_auth_key: !!opts.tailscale } as any)
  const wrapper = mount(OpenWebsiteModal, { props: { modelValue: true } })
  await flushPromises()
  await wrapper.find('textarea').setValue('https://example.com/app')
  return wrapper
}

async function launch(wrapper: VueWrapper) {
  // The Launch button is type=submit; jsdom won't submit a form from a click.
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  return launchMock.mock.calls[0][0]
}

describe('OpenWebsiteModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    vi.mocked(imagesApi.list).mockResolvedValue([FIREFOX, BRAVE])
    launchMock.mockResolvedValue({ id: 99 })
  })

  it('renders browsers as a logo gallery and preselects the first', async () => {
    const wrapper = await openModal()
    const cards = wrapper.findAll('.card')

    expect(cards).toHaveLength(2)
    expect(cards[0].classes()).toContain('selected')
    expect(cards[0].find('img').attributes('src')).toBe('https://logo/firefox.png')
    // No logo on Brave — falls back to an initial rather than a broken image.
    expect(cards[1].find('.logo-fallback').text()).toBe('B')
  })

  it('launches with the browser picked from the gallery', async () => {
    const wrapper = await openModal()
    await wrapper.findAll('.card')[1].trigger('click')

    const payload = await launch(wrapper)
    expect(payload.image_id).toBe(BRAVE.id)
    expect(payload.workspace_type).toBe('browser')
    expect(payload.name).toBe('example.com')
  })

  it('disables routing segments until the credential exists', async () => {
    const wrapper = await openModal()
    expect(segment(wrapper, 'Tailscale').attributes('disabled')).toBeDefined()
    expect(segment(wrapper, 'Gluetun').attributes('disabled')).toBeDefined()
    expect(segment(wrapper, 'Direct').classes()).toContain('on')
  })

  it('sends ts_* fields when Tailscale is selected', async () => {
    const wrapper = await openModal({ tailscale: true })
    await segment(wrapper, 'Tailscale').trigger('click')
    await wrapper.find('.ts-field input[type="text"]').setValue('us-nyc-1')
    await toggleFor(wrapper, 'Accept DNS').trigger('click')

    const payload = await launch(wrapper)
    expect(payload.use_tailscale).toBe(true)
    expect(payload.use_gluetun).toBe(false)
    expect(payload.ts_exit_node).toBe('us-nyc-1')
    expect(payload.ts_accept_routes).toBe(true)
    expect(payload.ts_accept_dns).toBe(false)
  })

  it('treats the modes as mutually exclusive', async () => {
    const wrapper = await openModal({ tailscale: true, gluetun: true })
    await segment(wrapper, 'Tailscale').trigger('click')
    await segment(wrapper, 'Gluetun').trigger('click')

    const payload = await launch(wrapper)
    expect(payload.use_gluetun).toBe(true)
    expect(payload.use_tailscale).toBe(false)
    // Tailscale-only fields must not ride along on a Gluetun launch.
    expect(payload.ts_exit_node).toBeUndefined()
  })

  it('offers "Discard when stopped" only once ephemeral is on', async () => {
    const wrapper = await openModal()
    expect(wrapper.findAll('[role="switch"]').some(t => t.text().includes('Discard when stopped'))).toBe(false)

    await toggleFor(wrapper, 'Ephemeral').trigger('click')
    await toggleFor(wrapper, 'Discard when stopped').trigger('click')

    const payload = await launch(wrapper)
    expect(payload.ephemeral).toBe(true)
    expect(payload.auto_remove).toBe(true)
  })

  it('clears auto_remove when ephemeral is switched back off', async () => {
    const wrapper = await openModal()
    await toggleFor(wrapper, 'Ephemeral').trigger('click')
    await toggleFor(wrapper, 'Discard when stopped').trigger('click')
    await toggleFor(wrapper, 'Ephemeral').trigger('click') // back off

    const payload = await launch(wrapper)
    expect(payload.ephemeral).toBe(false)
    // The API rejects auto_remove without ephemeral, so it must not survive.
    expect(payload.auto_remove).toBe(false)
  })
})
