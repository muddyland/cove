import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/auth', () => ({
  authApi: {
    twoFactor: {
      status: vi.fn(), totpSetup: vi.fn(), totpEnable: vi.fn(), totpDisable: vi.fn(), recoveryCodes: vi.fn(),
      passkeyOptions: vi.fn(), passkeyRegister: vi.fn(), passkeyDelete: vi.fn(),
    },
  },
}))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ toast: vi.fn() }) }))
vi.mock('@/utils/webauthn', async (orig) => ({
  ...(await orig<typeof import('@/utils/webauthn')>()),
  passkeysAvailable: () => true,
  createCredential: vi.fn().mockResolvedValue({ id: 'new-key' }),
}))

import TwoFactorPanel from '@/components/TwoFactorPanel.vue'
import { authApi } from '@/api/auth'
import { createCredential } from '@/utils/webauthn'
import { useAuthStore } from '@/stores/auth'

const off = { available: true, required: false, totp: false, recovery_codes_left: 0, passkeys: [] }
const tfa = authApi.twoFactor

function btn(w: ReturnType<typeof mount>, text: RegExp) {
  return w.findAll('button').find(b => text.test(b.text()))!
}

describe('TwoFactorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    useAuthStore().config = { oidc_enabled: false, oidc_only: false, oidc_provider_name: '', needs_setup: false, passkeys: true }
  })

  it('sets up an authenticator app and shows the recovery codes once', async () => {
    vi.mocked(tfa.status).mockResolvedValue(off)
    vi.mocked(tfa.totpSetup).mockResolvedValue({ secret: 'ABCDEFGH', uri: 'otpauth://x', qr: 'data:image/svg+xml;base64,AA', setup_token: 't' })
    vi.mocked(tfa.totpEnable).mockResolvedValue({ recovery_codes: ['AAAA-BBBB-CCCC-DDDD', 'EEEE-FFFF-GGGG-HHHH'] })
    const w = mount(TwoFactorPanel, { props: { username: 'bob' } })
    await flushPromises()
    await btn(w, /^Set up$/).trigger('click')
    await flushPromises()
    expect(w.find('img.qr').attributes('src')).toContain('data:image/svg+xml')
    expect(w.text()).toContain('ABCD EFGH')
    await w.find('input[autocomplete="one-time-code"]').setValue('123456')
    await w.find('input[type="password"]').setValue('pw')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(tfa.totpEnable).toHaveBeenCalledWith('t', '123456', undefined, 'pw')
    expect(w.text()).toContain('AAAA-BBBB-CCCC-DDDD')

    vi.mocked(tfa.status).mockResolvedValue({ ...off, totp: true, recovery_codes_left: 10 })
    await btn(w, /saved them/).trigger('click')
    await flushPromises()
    expect(w.text()).not.toContain('AAAA-BBBB-CCCC-DDDD')
    expect(w.text()).toContain('10 left')
  })

  it('adds a security key', async () => {
    vi.mocked(tfa.status).mockResolvedValue({ ...off, totp: true, recovery_codes_left: 10 })
    vi.mocked(tfa.passkeyOptions).mockResolvedValue({ options: { challenge: 'c' }, state: 's' })
    vi.mocked(tfa.passkeyRegister).mockResolvedValue({ passkey: { id: 1, name: 'YubiKey' } })
    const w = mount(TwoFactorPanel, { props: { username: 'bob', thinClient: true } })
    await flushPromises()
    await btn(w, /Add security key/).trigger('click')
    await w.find('input[maxlength="64"]').setValue('YubiKey')
    await w.find('input[type="password"]').setValue('pw')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(tfa.passkeyRegister).toHaveBeenCalledWith('s', { id: 'new-key' }, 'YubiKey', undefined, 'pw')
  })

  it('asks for the password before removing anything', async () => {
    vi.mocked(tfa.status).mockResolvedValue({
      ...off, totp: true, recovery_codes_left: 2,
      passkeys: [{ id: 7, name: 'YubiKey', created_at: '2026-10-30T00:00:00', last_used_at: null, transports: ['usb'] }],
    })
    const w = mount(TwoFactorPanel)
    await flushPromises()
    expect(w.find('.sub.warn').text()).toBe('2 left') // running low
    await w.findAll('li')[1].find('button').trigger('click')
    expect(w.find('.confirm label').text()).toContain('Remove “YubiKey”')
    await w.find('.confirm input').setValue('pw')
    await w.find('.confirm').trigger('submit')
    await flushPromises()
    expect(tfa.passkeyDelete).toHaveBeenCalledWith(7, 'pw')
  })
  it('says so when the key is already set up for the account', async () => {
    vi.mocked(tfa.status).mockResolvedValue({ ...off, totp: true, recovery_codes_left: 10 })
    vi.mocked(tfa.passkeyOptions).mockResolvedValue({ options: { challenge: 'c' } as never, state: 's' })
    vi.mocked(createCredential).mockRejectedValueOnce(new DOMException('dup', 'InvalidStateError'))
    const w = mount(TwoFactorPanel, { props: { username: 'bob' } })
    await flushPromises()
    await btn(w, /Add passkey/i).trigger('click')
    await w.find('input[type="password"]').setValue('pw')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain('That key is already set up for your account.')
    expect(tfa.passkeyRegister).not.toHaveBeenCalled()
  })
  it('keeps this browser signed in when the first factor ends the other sessions', async () => {
    vi.mocked(tfa.status).mockResolvedValue(off)
    vi.mocked(tfa.totpSetup).mockResolvedValue({ secret: 'ABCDEFGH', uri: 'otpauth://x', qr: 'data:,', setup_token: 't' })
    vi.mocked(tfa.totpEnable).mockResolvedValue({ recovery_codes: ['AAAA-BBBB-CCCC-DDDD'], access_token: 'fresh' })
    const w = mount(TwoFactorPanel, { props: { username: 'bob' } })
    await flushPromises()
    await btn(w, /Set up/i).trigger('click')
    await flushPromises()
    await w.find('input[autocomplete="one-time-code"]').setValue('123456')
    await w.find('input[type="password"]').setValue('pw')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(useAuthStore().token).toBe('fresh')
  })
})
