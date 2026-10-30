import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

const query: Record<string, string> = {}
const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }), useRoute: () => ({ query }) }))
vi.mock('@/api/thinClients', () => ({ thinClientsApi: { self: vi.fn() } }))
vi.mock('@/api/auth', () => ({
  authApi: {
    login: vi.fn(), loginCode: vi.fn(), passkeyOptions: vi.fn(), passkeyLogin: vi.fn(), me: vi.fn(),
    config: vi.fn(), refresh: vi.fn(), logout: vi.fn(),
    twoFactor: { totpSetup: vi.fn(), totpEnable: vi.fn(), passkeyOptions: vi.fn(), passkeyRegister: vi.fn() },
  },
}))
vi.mock('@/utils/webauthn', async (orig) => ({
  ...(await orig<typeof import('@/utils/webauthn')>()),
  passkeysAvailable: () => true,
  getAssertion: vi.fn().mockResolvedValue({ id: 'k1' }),
}))

import LoginView from '@/views/LoginView.vue'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api/auth'
import { thinClientsApi } from '@/api/thinClients'

const me = { id: 2, username: 'bob' }

async function mountLogin() {
  const wrapper = mount(LoginView)
  await flushPromises()
  return wrapper
}

function button(w: ReturnType<typeof mount>, text: RegExp) {
  return w.findAll('button').find(b => text.test(b.text()))!
}

describe('LoginView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    for (const k of Object.keys(query)) delete query[k]
    vi.mocked(thinClientsApi.self).mockRejectedValue(new Error('404'))
    vi.mocked(authApi.me).mockResolvedValue(me as any)
    useAuthStore().config = { oidc_enabled: false, oidc_only: false, oidc_provider_name: '', needs_setup: false, passkeys: true }
  })

  it('offers passkeys only when the server can use them (COVE_APP_ORIGIN set)', async () => {
    expect((await mountLogin()).text()).toMatch(/passkey/i)
    useAuthStore().config!.passkeys = false
    const wrapper = await mountLogin()
    expect(wrapper.text()).not.toMatch(/passkey/i)
  })

  it('says why when a local-network-only session was ended', async () => {
    query.error = 'lan_only'
    const wrapper = await mountLogin()
    expect(wrapper.find('.form-error').text()).toContain('can only be used from the local network')
  })

  it('does not bounce back to the identity provider in that case (it would loop)', async () => {
    const auth = useAuthStore()
    auth.config = { oidc_enabled: true, oidc_only: true, oidc_provider_name: 'Authentik', needs_setup: false }
    const spy = vi.spyOn(window, 'location', 'get')
    const loc = { href: '/app/login?error=lan_only' } as Location
    spy.mockReturnValue(loc)
    query.error = 'lan_only'
    const wrapper = await mountLogin()
    expect(loc.href).toBe('/app/login?error=lan_only')
    expect(wrapper.text()).toContain('can only be used from the local network')
    spy.mockRestore()
  })

  it('asks for the authenticator code after the password', async () => {
    vi.mocked(authApi.login).mockResolvedValue({ mfa_required: true, mfa_token: 'm1', methods: ['totp', 'recovery'] })
    vi.mocked(authApi.loginCode).mockResolvedValue({ access_token: 'a1' })
    const wrapper = await mountLogin()
    await wrapper.find('input[autocomplete^="username"]').setValue('bob')
    await wrapper.find('input[type="password"]').setValue('pw')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(push).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('6-digit code')
    expect(wrapper.text()).not.toMatch(/Use a passkey/) // not one of this account's methods

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('123456')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(authApi.loginCode).toHaveBeenCalledWith('m1', '123456')
    expect(push).toHaveBeenCalledWith('/app')
  })

  it('switches to a recovery code', async () => {
    vi.mocked(authApi.login).mockResolvedValue({ mfa_required: true, mfa_token: 'm1', methods: ['totp', 'recovery'] })
    const wrapper = await mountLogin()
    await wrapper.find('input[autocomplete^="username"]').setValue('bob')
    await wrapper.find('input[type="password"]').setValue('pw')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    await button(wrapper, /recovery code/).trigger('click')
    expect(wrapper.find('input[autocomplete="one-time-code"]').attributes('placeholder')).toBe('XXXX-XXXX-XXXX-XXXX')
  })

  it('on a thin client asks for the username first, then a password or a security key', async () => {
    vi.mocked(thinClientsApi.self).mockResolvedValue({ name: 'Desk' } as any)
    vi.mocked(authApi.passkeyOptions).mockResolvedValue({ options: { challenge: 'x' }, state: 's1' })
    vi.mocked(authApi.passkeyLogin).mockResolvedValue({ access_token: 'a2' })
    const wrapper = await mountLogin()
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    await wrapper.find('input[autocomplete="username"]').setValue('bob')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('.who').text()).toContain('bob')
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)

    await button(wrapper, /USE SECURITY KEY/).trigger('click')
    await flushPromises()
    expect(authApi.passkeyOptions).toHaveBeenCalledWith('bob')
    expect(authApi.passkeyLogin).toHaveBeenCalledWith('s1', { id: 'k1' })
    expect(authApi.login).not.toHaveBeenCalled() // no password, and no TOTP after a key
    expect(push).toHaveBeenCalledWith('/app')
  })

  it('sets up a second factor when the account requires one', async () => {
    vi.mocked(authApi.login).mockResolvedValue({ mfa_setup_required: true, mfa_token: 'setup1' })
    const wrapper = await mountLogin()
    await wrapper.find('input[autocomplete^="username"]').setValue('bob')
    await wrapper.find('input[type="password"]').setValue('pw')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('needs two-factor sign-in')
    expect(wrapper.text()).toContain('Authenticator app')
    expect(wrapper.text()).toContain('Passkey or security key')
    // The password was just entered: setting up here doesn't ask for it again.
    vi.mocked(authApi.twoFactor.totpSetup).mockResolvedValue({ secret: 'AB', uri: 'otpauth://x', qr: 'data:,', setup_token: 't' })
    await wrapper.findAll('.choice')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.tfa-setup input[type="password"]').exists()).toBe(false)
  })
})
