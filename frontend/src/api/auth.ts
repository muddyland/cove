import { api } from './client'
import type {
  AuthConfig, EnrolResult, LoginStep, TotpSetup, TwoFactorStatus, User, WebAuthnChallenge,
} from '@/types'

/** While finishing a required 2FA setup during sign-in, enrolment calls carry
 *  the sign-in's token instead of a session. */
const enrol = (mfaToken?: string) => (mfaToken ? { mfa_token: mfaToken } : {})

export const authApi = {
  config: () => api.get<AuthConfig>('/auth/config'),
  setup: (username: string, password: string) =>
    api.post<{ access_token: string }>('/auth/setup', { username, password }),
  login: (username: string, password: string) => api.post<LoginStep>('/auth/login', { username, password }),
  /** Second step: a TOTP code or a recovery code. */
  loginCode: (mfa_token: string, code: string) =>
    api.post<{ access_token: string; recovery_codes_left?: number }>('/auth/login/2fa', { mfa_token, code }),
  passkeyOptions: (username: string) => api.post<WebAuthnChallenge>('/auth/login/passkey/options', { username }),
  passkeyLogin: (state: string, credential: unknown) =>
    api.post<{ access_token: string }>('/auth/login/passkey', { state, credential }),
  refresh: () => api.post<{ access_token: string }>('/auth/refresh'),
  me: () => api.get<User>('/auth/me'),
  logout: () => api.post('/auth/logout'),
  changePassword: (current_password: string, new_password: string) =>
    api.post('/auth/change-password', { current_password, new_password }),

  twoFactor: {
    status: () => api.get<TwoFactorStatus>('/auth/2fa'),
    totpSetup: (mfaToken?: string) => api.post<TotpSetup>('/auth/2fa/totp/setup', enrol(mfaToken)),
    /** `password` is needed when adding it from a session (Preferences), not during sign-in. */
    totpEnable: (setup_token: string, code: string, mfaToken?: string, password?: string) =>
      api.post<EnrolResult>('/auth/2fa/totp/enable', { setup_token, code, password, ...enrol(mfaToken) }),
    totpDisable: (password: string) => api.post('/auth/2fa/totp/disable', { password }),
    recoveryCodes: (password: string) => api.post<{ recovery_codes: string[] }>('/auth/2fa/recovery-codes', { password }),
    passkeyOptions: (mfaToken?: string) => api.post<WebAuthnChallenge>('/auth/2fa/passkeys/options', enrol(mfaToken)),
    passkeyRegister: (state: string, credential: unknown, name: string, mfaToken?: string, password?: string) =>
      api.post<EnrolResult & { passkey: { id: number; name: string } }>(
        '/auth/2fa/passkeys', { state, credential, name, password, ...enrol(mfaToken) },
      ),
    passkeyDelete: (id: number, password: string) => api.post(`/auth/2fa/passkeys/${id}/delete`, { password }),
  },
}
