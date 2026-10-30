<template>
  <div class="tfa-setup">
    <RecoveryCodesBox v-if="codes" :codes="codes" :username="username" @done="emit('done', result)" />

    <template v-else-if="mode === 'choose'">
      <div class="choices">
        <button v-if="!hasTotp" type="button" class="choice" @click="startTotp">
          <Smartphone :size="20" />
          <span><strong>Authenticator app</strong><small>A 6-digit code from an app such as Aegis, 2FAS or 1Password.</small></span>
        </button>
        <button v-if="canPasskey" type="button" class="choice" @click="mode = 'passkey'">
          <KeyRound :size="20" />
          <span><strong>{{ thinClient ? 'Security key' : 'Passkey or security key' }}</strong>
            <small>{{ thinClient ? 'A USB key such as a YubiKey, with its PIN.' : 'Your device\'s passkey, or a USB / NFC key, with a PIN or fingerprint.' }}</small></span>
        </button>
      </div>
      <p v-if="!canPasskey" class="hint">Passkeys need Cove to be opened by its domain name in a browser that supports them.</p>
    </template>

    <form v-else-if="mode === 'totp'" class="form" @submit.prevent="confirmTotp">
      <LoadingSpinner v-if="!totp" block />
      <template v-else>
        <div class="qr-row">
          <img :src="totp.qr" alt="QR code to scan with your authenticator app" class="qr" />
          <div class="qr-text">
            <p class="hint">Scan this with your authenticator app, or enter the key by hand:</p>
            <code class="secret">{{ grouped(totp.secret) }}</code>
          </div>
        </div>
        <div class="form-group">
          <label>Code from the app</label>
          <input v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" required autofocus
                 placeholder="123456" />
        </div>
        <div v-if="!mfaToken" class="form-group">
          <label>Your password</label>
          <input v-model="password" type="password" autocomplete="current-password" required />
        </div>
      </template>
      <div v-if="error" class="form-error">⚠ {{ error }}</div>
      <div class="actions">
        <NeonButton type="button" variant="ghost" @click="reset">Back</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="busy" :disabled="!totp">Turn on</NeonButton>
      </div>
    </form>

    <form v-else class="form" @submit.prevent="addPasskey">
      <div class="form-group">
        <label>Name</label>
        <input v-model="keyName" maxlength="64" :placeholder="thinClient ? 'YubiKey' : 'Laptop, YubiKey…'" />
      </div>
      <div v-if="!mfaToken" class="form-group">
        <label>Your password</label>
        <input v-model="password" type="password" autocomplete="current-password" required />
      </div>
      <p class="hint">
        {{ thinClient ? 'Plug in your security key, then touch it when it blinks. It asks for its PIN.' :
          'Your browser asks which passkey or key to use, and for its PIN or fingerprint.' }}
      </p>
      <div v-if="error" class="form-error">⚠ {{ error }}</div>
      <div class="actions">
        <NeonButton type="button" variant="ghost" @click="reset">Back</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="busy">Add</NeonButton>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { KeyRound, Smartphone } from 'lucide-vue-next'
import NeonButton from './NeonButton.vue'
import LoadingSpinner from './LoadingSpinner.vue'
import RecoveryCodesBox from './RecoveryCodesBox.vue'
import { authApi } from '@/api/auth'
import { createCredential, isAlreadyRegistered, isCancelled, passkeysAvailable } from '@/utils/webauthn'
import { useAuthStore } from '@/stores/auth'
import type { EnrolResult, TotpSetup } from '@/types'

const props = defineProps<{
  /** Finishing a required setup during sign-in. */
  mfaToken?: string
  username?: string
  hasTotp?: boolean
  thinClient?: boolean
  /** Start straight in one mode (Preferences' buttons). */
  start?: 'totp' | 'passkey'
}>()
const emit = defineEmits<{ done: [result: EnrolResult] }>()

const mode = ref<'choose' | 'totp' | 'passkey'>('choose')
const totp = ref<TotpSetup | null>(null)
const code = ref('')
const keyName = ref('')
// Adding a factor from a signed-in session needs the password (a stolen session
// mustn't add its own); during sign-in the password was just entered.
const password = ref('')
const busy = ref(false)
const error = ref('')
const codes = ref<string[] | null>(null)
const result = ref<EnrolResult>({})
const auth = useAuthStore()
// The browser can, and the server has a domain name to tie passkeys to.
const canPasskey = computed(() => passkeysAvailable() && !!auth.config?.passkeys)

function grouped(s: string) {
  return s.replace(/(.{4})/g, '$1 ').trim()
}

function reset() {
  mode.value = 'choose'
  totp.value = null
  code.value = ''
  error.value = ''
}

async function startTotp() {
  mode.value = 'totp'
  error.value = ''
  try {
    totp.value = await authApi.twoFactor.totpSetup(props.mfaToken)
  } catch (e: any) {
    error.value = e.message
  }
}

function finished(res: EnrolResult) {
  // A first factor added from Preferences ends every other session and hands
  // this one a fresh token (during sign-in, LoginView signs in with it).
  if (res.access_token && !props.mfaToken) auth.setToken(res.access_token)
  result.value = res
  if (res.recovery_codes?.length) codes.value = res.recovery_codes
  else emit('done', res)
}

async function confirmTotp() {
  if (!totp.value) return
  busy.value = true
  error.value = ''
  try {
    finished(await authApi.twoFactor.totpEnable(totp.value.setup_token, code.value, props.mfaToken, password.value || undefined))
  } catch (e: any) {
    error.value = e.message
  } finally {
    busy.value = false
    password.value = ''
  }
}

async function addPasskey() {
  busy.value = true
  error.value = ''
  try {
    const { options, state } = await authApi.twoFactor.passkeyOptions(props.mfaToken)
    const credential = await createCredential(options)
    finished(await authApi.twoFactor.passkeyRegister(
      state, credential, keyName.value.trim(), props.mfaToken, password.value || undefined,
    ))
  } catch (e: any) {
    error.value = isAlreadyRegistered(e)
      ? 'That key is already set up for your account.'
      : isCancelled(e) ? 'Cancelled, or the key didn\'t answer in time.' : e.message
  } finally {
    busy.value = false
    password.value = ''
  }
}

if (props.start === 'totp') startTotp()
else if (props.start === 'passkey') mode.value = 'passkey'
</script>

<style scoped>
.tfa-setup { display: flex; flex-direction: column; gap: 14px; }
.choices { display: flex; flex-direction: column; gap: 10px; }
.choice {
  display: flex; gap: 14px; align-items: flex-start; text-align: left; cursor: pointer;
  padding: 14px; border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--surface-2); color: var(--accent); font: inherit;
}
.choice:hover, .choice:focus-visible { border-color: var(--accent); }
.choice span { display: flex; flex-direction: column; gap: 4px; }
.choice strong { color: var(--text); font-size: 14px; font-weight: 600; }
.choice small { color: var(--text-muted); font-size: 12px; line-height: 1.4; }
.form { display: flex; flex-direction: column; gap: 14px; }
.qr-row { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.qr { width: 168px; height: 168px; border-radius: var(--radius-sm); background: #fff; }
.qr-text { flex: 1; min-width: 180px; display: flex; flex-direction: column; gap: 8px; }
.secret { font-family: var(--font-mono); font-size: 13px; color: var(--text); word-break: break-all; letter-spacing: 1px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.hint { color: var(--text-muted); font-size: 13px; line-height: 1.5; margin: 0; }
</style>
