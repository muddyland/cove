import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { ThinClient } from '@/types'

vi.mock('@/api/thinClients', () => ({
  thinClientsApi: { list: vi.fn(), screenshot: vi.fn(), workspaceOptions: vi.fn() },
}))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ toast: vi.fn() }) }))
vi.mock('@/components/AppShell.vue', () => ({ default: { template: '<div><slot /></div>' } }))
vi.mock('@/components/BaseModal.vue', () => ({
  default: { props: ['modelValue'], template: '<div v-if="modelValue"><slot /></div>' },
}))
// Encoding is deferred and released by the test, so two encodes can overlap the
// way two real FileReader reads do.
const encodes: Record<string, () => void> = {}
vi.mock('@/utils/dataUrl', () => ({
  toDataUrl: (b: { tag: string }) =>
    new Promise<string>(resolve => { encodes[b.tag] = () => resolve(`data:image/png;base64,${b.tag}`) }),
}))

import { thinClientsApi } from '@/api/thinClients'
import AdminThinClientsView from '@/views/AdminThinClientsView.vue'

function device(id: number, name: string, state: Record<string, unknown> = {}): ThinClient {
  return {
    id, public_id: `p${id}`, name, location: null, status: 'enrolled', online: true,
    enrolled_at: null, last_seen_at: null, created_at: '2026-10-01T00:00:00Z',
    policy: {} as ThinClient['policy'], policy_version: 1, applied_policy_version: 1,
    state: { os: 'Debian GNU/Linux 13 (trixie)', ...state } as ThinClient['state'],
    session_username: null, session_started_at: null, keep_awake_until: null, bound_at: null,
    screenshot_at: '2026-10-30T00:00:00Z', pending_commands: 0, latest_agent_version: null,
  }
}

describe('AdminThinClientsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('shows every screenshot when they finish loading out of order', async () => {
    vi.mocked(thinClientsApi.list).mockResolvedValue([device(1, 'Lounge'), device(2, 'Desk')])
    // The first device's screenshot arrives last: before the fix, the second
    // load's write was overwritten by a stale copy of the map.
    const pending: Record<number, (b: unknown) => void> = {}
    vi.mocked(thinClientsApi.screenshot).mockImplementation(
      (id: number) => new Promise(resolve => { pending[id] = resolve as (b: unknown) => void }) as Promise<Blob>,
    )
    const wrapper = mount(AdminThinClientsView)
    await flushPromises()
    // Both downloads land, both encodes are in flight, and they finish in
    // reverse order.
    pending[1]({ tag: 'one' })
    pending[2]({ tag: 'two' })
    await flushPromises()
    encodes.two()
    await flushPromises()
    encodes.one()
    await flushPromises()
    const srcs = wrapper.findAll('article .shot img').map(i => i.attributes('src'))
    expect(srcs.sort()).toEqual(['data:image/png;base64,one', 'data:image/png;base64,two'])
    wrapper.unmount()
  })

  it('spaces the system facts and keeps the full line in a tooltip', async () => {
    vi.mocked(thinClientsApi.list).mockResolvedValue([device(1, 'Desk', { upgradable: 7, reboot_required: true })])
    vi.mocked(thinClientsApi.screenshot).mockResolvedValue(null as unknown as Blob)
    const wrapper = mount(AdminThinClientsView)
    await flushPromises()
    const dd = wrapper.findAll('dd').find(d => d.text().includes('updates'))!
    expect(dd.text().replace(/\s+/g, ' ')).toContain('(trixie) · 7 updates · reboot needed')
    expect(dd.attributes('title')).toBe('Debian GNU/Linux 13 (trixie) · 7 updates · reboot needed')
    wrapper.unmount()
  })
})
