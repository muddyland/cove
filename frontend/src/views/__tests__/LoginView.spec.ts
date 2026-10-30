import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

const query: Record<string, string> = {}
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => ({ query }) }))

import LoginView from '@/views/LoginView.vue'
import { useAuthStore } from '@/stores/auth'

describe('LoginView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    for (const k of Object.keys(query)) delete query[k]
  })

  it('says why when a local-network-only session was ended', async () => {
    query.error = 'lan_only'
    const wrapper = mount(LoginView)
    await flushPromises()
    expect(wrapper.find('.form-error').text()).toContain('can only be used from the local network')
  })

  it('does not bounce back to the identity provider in that case (it would loop)', async () => {
    const auth = useAuthStore()
    auth.config = { oidc_enabled: true, oidc_only: true, oidc_provider_name: 'Authentik', needs_setup: false }
    const assign = vi.spyOn(window, 'location', 'get')
    const loc = { href: '/app/login?error=lan_only' } as Location
    assign.mockReturnValue(loc)
    query.error = 'lan_only'
    const wrapper = mount(LoginView)
    await flushPromises()
    expect(loc.href).toBe('/app/login?error=lan_only')
    expect(wrapper.text()).toContain('can only be used from the local network')
    assign.mockRestore()
  })
})
