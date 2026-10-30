<template>
  <div class="auth-page">
    <div class="grid-bg" />
    <div class="auth-card" :class="{ wide: step === 'setup' }">
      <div class="auth-logo">
        <img class="logo-icon" src="/favicon.svg" alt="" />
        <span>COVE</span>
      </div>
      <div class="auth-subtitle">// SECURE ACCESS TERMINAL</div>

      <template v-if="step === 'credentials' || step === 'username' || step === 'secret'">
        <a v-if="auth.oidcEnabled" :href="oidcLoginUrl" class="oidc-btn">
          <LogIn :size="15" /> {{ auth.oidcOnly ? 'CONTINUE TO' : 'AUTH VIA' }} {{ auth.oidcProviderName.toUpperCase() }}
        </a>

        <!-- OIDC-only mode: redirecting to the IdP; local form is hidden. -->
        <div v-if="auth.oidcOnly && error" class="form-error">⚠ {{ error }}</div>
        <div v-else-if="auth.oidcOnly" class="redirect-note">// redirecting to {{ auth.oidcProviderName }}…</div>

        <template v-else>
          <div v-if="auth.oidcEnabled" class="divider"><span>— OR —</span></div>

          <!-- Ordinary browser: username and password together, passkey as an alternative. -->
          <form v-if="step === 'credentials'" @submit.prevent="handleLogin" class="form">
            <div class="form-group">
              <label>Username</label>
              <input v-model="username" required autocomplete="username webauthn" autofocus />
            </div>
            <div class="form-group">
              <label>Password</label>
              <input v-model="password" type="password" required autocomplete="current-password" />
            </div>
            <div v-if="error" class="form-error">⚠ {{ error }}</div>
            <NeonButton type="submit" variant="primary" :loading="loading" style="width:100%">
              AUTHENTICATE
            </NeonButton>
            <button v-if="canPasskey" type="button" class="link-btn" :disabled="loading" @click="handlePasskey">
              <KeyRound :size="14" /> Sign in with a passkey
            </button>
          </form>

          <!-- Thin client: username first, then a password or a security key. -->
          <form v-else-if="step === 'username'" @submit.prevent="step = 'secret'; error = ''" class="form">
            <div class="form-group">
              <label>Username</label>
              <input v-model="username" required autocomplete="username" autofocus />
            </div>
            <div v-if="error" class="form-error">⚠ {{ error }}</div>
            <NeonButton type="submit" variant="primary" style="width:100%">NEXT</NeonButton>
          </form>

          <form v-else @submit.prevent="handleLogin" class="form">
            <div class="who">
              <span>{{ username }}</span>
              <button type="button" class="link-btn small" @click="backToStart">Not you?</button>
            </div>
            <div class="form-group">
              <label>Password</label>
              <input v-model="password" type="password" required autocomplete="current-password" autofocus />
            </div>
            <div v-if="error" class="form-error">⚠ {{ error }}</div>
            <NeonButton type="submit" variant="primary" :loading="loading" style="width:100%">
              AUTHENTICATE
            </NeonButton>
            <template v-if="canPasskey">
              <div class="divider"><span>— OR —</span></div>
              <NeonButton type="button" variant="secondary" :loading="keyBusy" style="width:100%" @click="handlePasskey">
                <KeyRound :size="15" /> USE SECURITY KEY
              </NeonButton>
            </template>
          </form>
        </template>
      </template>

      <!-- Second step after a password: TOTP, recovery code, or a passkey. -->
      <form v-else-if="step === 'code'" @submit.prevent="handleCode" class="form">
        <p class="step-note">
          <template v-if="useRecovery">Enter one of your recovery codes.</template>
          <template v-else>Enter the 6-digit code from your authenticator app.</template>
        </p>
        <div class="form-group">
          <label>{{ useRecovery ? 'Recovery code' : 'Code' }}</label>
          <input v-model="code" :inputmode="useRecovery ? 'text' : 'numeric'" autocomplete="one-time-code"
                 :maxlength="useRecovery ? 24 : 8" required autofocus :placeholder="useRecovery ? 'XXXX-XXXX-XXXX-XXXX' : '123456'" />
        </div>
        <div v-if="error" class="form-error">⚠ {{ error }}</div>
        <NeonButton type="submit" variant="primary" :loading="loading" style="width:100%">VERIFY</NeonButton>
        <div class="alt-row">
          <button v-if="methods.includes('totp')" type="button" class="link-btn small" @click="useRecovery = !useRecovery; code = ''">
            {{ useRecovery ? 'Use the authenticator app' : 'Use a recovery code' }}
          </button>
          <button v-if="methods.includes('passkey') && canPasskey" type="button" class="link-btn small" :disabled="keyBusy" @click="handlePasskey">
            <KeyRound :size="13" /> Use {{ thinClient ? 'security key' : 'a passkey' }}
          </button>
          <button type="button" class="link-btn small" @click="backToStart">Start over</button>
        </div>
      </form>

      <!-- An admin requires 2FA and this account has none yet. -->
      <div v-else-if="step === 'setup'" class="form">
        <p class="step-note">
          Your account needs two-factor sign-in. Set up one of these to finish signing in.
        </p>
        <TwoFactorSetup :mfa-token="mfaToken" :username="username" :thin-client="thinClient" @done="finishSetup" />
        <button type="button" class="link-btn small" @click="backToStart">Cancel</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThinClientStore } from '@/stores/thinClient'
import { authApi } from '@/api/auth'
import NeonButton from '@/components/NeonButton.vue'
import TwoFactorSetup from '@/components/TwoFactorSetup.vue'
import { KeyRound, LogIn } from 'lucide-vue-next'
import { getAssertion, isCancelled, passkeysAvailable } from '@/utils/webauthn'
import type { EnrolResult } from '@/types'

const auth = useAuthStore()
const thinClientStore = useThinClientStore()
const router = useRouter()
const route = useRoute()

type Step = 'credentials' | 'username' | 'secret' | 'code' | 'setup'
const step = ref<Step>('credentials')
const username = ref('')
const password = ref('')
const code = ref('')
const error = ref('')
const loading = ref(false)
const keyBusy = ref(false)
const mfaToken = ref('')
const methods = ref<string[]>([])
const useRecovery = ref(false)
const thinClient = computed(() => !!thinClientStore.info)
const canPasskey = computed(() => passkeysAvailable() && !!auth.config?.passkeys)
const oidcLoginUrl = '/api/auth/oidc/login'

onMounted(() => {
  // A kiosk asks for the username first, then a password or a security key.
  thinClientStore.load().then(() => {
    if (thinClientStore.info && step.value === 'credentials') step.value = 'username'
  })
  // Sent back here because this account is local network only (an SSO sign-in
  // from outside, or a session that left the LAN).
  if (route.query.error === 'lan_only') {
    error.value = 'This account can only be used from the local network.'
    return // and don't bounce straight back to the IdP: that would loop
  }
  // OIDC-only: skip the page and go straight to the IdP. The button remains as a
  // fallback if the redirect is blocked.
  if (auth.oidcOnly && auth.oidcEnabled) {
    window.location.href = oidcLoginUrl
  }
})

function backToStart() {
  step.value = thinClient.value ? 'username' : 'credentials'
  password.value = ''
  code.value = ''
  mfaToken.value = ''
  useRecovery.value = false
  error.value = ''
}

async function done(accessToken: string) {
  await auth.completeSignIn(accessToken)
  router.push('/app')
}

async function handleLogin() {
  error.value = ''
  loading.value = true
  try {
    const next = await auth.login(username.value, password.value)
    password.value = ''
    if (!next) {
      router.push('/app')
    } else if ('mfa_required' in next) {
      mfaToken.value = next.mfa_token
      methods.value = next.methods
      useRecovery.value = !next.methods.includes('totp')
      step.value = 'code'
      // A key-only account on a kiosk: go straight to the key prompt.
      if (!next.methods.includes('totp') && next.methods.includes('passkey') && canPasskey.value) handlePasskey()
    } else {
      mfaToken.value = next.mfa_token
      step.value = 'setup'
    }
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function handleCode() {
  error.value = ''
  loading.value = true
  try {
    const res = await authApi.loginCode(mfaToken.value, code.value)
    await done(res.access_token)
  } catch (e: any) {
    error.value = e.message
    code.value = ''
    if (/Sign in again|expired/.test(e.message)) {
      const msg = e.message
      backToStart()
      error.value = msg
    }
  } finally {
    loading.value = false
  }
}

async function handlePasskey() {
  if (!username.value.trim()) {
    error.value = 'Enter your username first.'
    return
  }
  error.value = ''
  keyBusy.value = true
  try {
    const { options, state } = await authApi.passkeyOptions(username.value.trim())
    const credential = await getAssertion(options)
    const res = await authApi.passkeyLogin(state, credential)
    await done(res.access_token)
  } catch (e: any) {
    error.value = isCancelled(e)
      ? 'No passkey or security key for this account answered. Check the username, or try again.'
      : e.message
  } finally {
    keyBusy.value = false
  }
}

async function finishSetup(res: EnrolResult) {
  if (res.access_token) await done(res.access_token)
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
  position: relative;
  overflow: hidden;
}

.grid-bg {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(0, 245, 255, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 245, 255, 0.03) 1px, transparent 1px);
  background-size: 40px 40px;
  mask-image: radial-gradient(ellipse at center, black 40%, transparent 80%);
}

.auth-card {
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  padding: 40px;
  width: 380px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  box-shadow: var(--glow), var(--shadow);
  position: relative;
  z-index: 1;
}
/* Corner cuts */
.auth-card::before, .auth-card::after {
  content: '';
  position: absolute;
  width: 12px; height: 12px;
  border-color: var(--accent);
  border-style: solid;
}
.auth-card::before { top: -1px; right: -1px; border-width: 2px 2px 0 0; border-radius: 0 var(--radius) 0 0; }
.auth-card::after  { bottom: -1px; left: -1px; border-width: 0 0 2px 2px; border-radius: 0 0 0 var(--radius); }

.auth-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--accent);
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 6px;
  text-shadow: var(--glow);
}
.logo-icon {
  width: 44px; height: 44px;
  display: block;
}

.auth-subtitle {
  text-align: center;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 2px;
  color: var(--text-muted);
}

.redirect-note {
  text-align: center;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 1px;
  color: var(--text-muted);
}

.oidc-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  padding: 10px;
  background: var(--accent-dim);
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1.5px;
  text-decoration: none;
  transition: all 0.15s;
}
.oidc-btn:hover {
  background: rgba(0, 245, 255, 0.15);
  box-shadow: var(--glow-sm);
  color: #fff;
  text-shadow: none;
}

.divider {
  text-align: center;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 3px;
}

.form { display: flex; flex-direction: column; gap: 14px; }
.auth-card.wide { max-width: 520px; }
.link-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  background: none; border: none; color: var(--accent); cursor: pointer; font: inherit; font-size: 13px; padding: 4px;
}
.link-btn:disabled { opacity: 0.5; cursor: default; }
.link-btn.small { font-size: 12px; color: var(--text-muted); }
.link-btn.small:hover { color: var(--accent); }
.alt-row { display: flex; flex-wrap: wrap; gap: 4px 12px; justify-content: center; }
.step-note { color: var(--text-muted); font-size: 13px; line-height: 1.5; margin: 0; }
.who { display: flex; align-items: center; justify-content: space-between; font-family: var(--font-mono); color: var(--text); }
</style>
