import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('@/api/extension', () => ({
  extensionApi: { info: vi.fn(), download: vi.fn() },
}))
// Not under test here, and each pulls in its own API calls.
vi.mock('@/components/TasksMenu.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/components/DocsModal.vue', () => ({ default: { template: '<div />' } }))

import { extensionApi } from '@/api/extension'
import { useAuthStore } from '@/stores/auth'
import AppShell from '@/components/AppShell.vue'
import type { User } from '@/types'

function makeRouter() {
  const r = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:p(.*)*', component: { template: '<div />' } }],
  })
  r.push('/app/')
  return r
}

async function openUserMenu() {
  const router = makeRouter()
  await router.isReady()
  const wrapper = mount(AppShell, { global: { plugins: [router] }, attachTo: document.body })
  await wrapper.get('.user-chip').trigger('click')
  await flushPromises()
  return wrapper
}

describe('AppShell user menu', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    setActivePinia(createPinia())
    useAuthStore().user = { id: 1, username: 'me', is_admin: false, auth_provider: 'local' } as User
  })

  it('offers the browser extension, asking the server only once', async () => {
    vi.mocked(extensionApi.info).mockResolvedValue({
      available: true, version: '0.2.0', size_bytes: 40 * 1024, filename: 'open-in-cove-0.2.0.zip',
    })
    const wrapper = await openUserMenu()
    const item = wrapper.findAll('.user-menu-item').find(i => i.text().includes('Browser extension'))!
    expect(item).toBeTruthy()

    await item.trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('Download v0.2.0')
    expect(document.body.textContent).toContain('40 KB')
    expect(document.body.textContent).toContain(window.location.origin)

    // Re-opening the menu doesn't refetch.
    await wrapper.get('.user-chip').trigger('click')
    await wrapper.get('.user-chip').trigger('click')
    await flushPromises()
    expect(extensionApi.info).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('shows no extension item when the build has none, or the request fails', async () => {
    vi.mocked(extensionApi.info).mockResolvedValue({ available: false, version: null, size_bytes: 0, filename: '' })
    let wrapper = await openUserMenu()
    expect(wrapper.text()).not.toContain('Browser extension')
    expect(wrapper.text()).toContain('Preferences')
    wrapper.unmount()

    vi.mocked(extensionApi.info).mockRejectedValue(new Error('boom'))
    wrapper = await openUserMenu()
    expect(wrapper.text()).not.toContain('Browser extension')
    wrapper.unmount()
  })
})
