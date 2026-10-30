<template>
  <section class="panel tfa">
    <h3>TWO-FACTOR SIGN-IN</h3>
    <LoadingSpinner v-if="!status" block />
    <template v-else>
      <p class="hint">
        A second step after your password: a code from an authenticator app, or a passkey or security
        key (which also signs you in without a password).
        <strong v-if="status.required"> Required for your account.</strong>
      </p>

      <TwoFactorSetup
        v-if="adding"
        :key="adding"
        :start="adding"
        :username="username"
        :has-totp="status.totp"
        :thin-client="thinClient"
        @done="afterAdd"
      />
      <RecoveryCodesBox v-else-if="newCodes" :codes="newCodes" :username="username" @done="newCodes = null" />

      <template v-else>
        <ul class="items">
          <li>
            <Smartphone :size="16" class="ic" />
            <div class="main">
              <span class="name">Authenticator app</span>
              <span class="sub" :class="{ on: status.totp }">{{ status.totp ? 'On' : 'Not set up' }}</span>
            </div>
            <NeonButton v-if="!status.totp" variant="ghost" @click="adding = 'totp'">Set up</NeonButton>
            <NeonButton v-else variant="ghost" @click="ask('totp')">Remove</NeonButton>
          </li>
          <li v-for="k in status.passkeys" :key="k.id">
            <KeyRound :size="16" class="ic" />
            <div class="main">
              <span class="name">{{ k.name }}</span>
              <span class="sub">Added {{ fmt(k.created_at) }}<template v-if="k.last_used_at"> · last used {{ fmt(k.last_used_at) }}</template></span>
            </div>
            <NeonButton variant="ghost" @click="ask('passkey', k.id, k.name)">Remove</NeonButton>
          </li>
          <li v-if="status.totp || status.passkeys.length">
            <ListOrdered :size="16" class="ic" />
            <div class="main">
              <span class="name">Recovery codes</span>
              <span class="sub" :class="{ warn: status.recovery_codes_left < 3 }">{{ status.recovery_codes_left }} left</span>
            </div>
            <NeonButton variant="ghost" @click="ask('codes')">New codes</NeonButton>
          </li>
        </ul>

        <form v-if="confirming" class="confirm" @submit.prevent="runConfirmed">
          <label>{{ confirmLabel }} — enter your password</label>
          <div class="confirm-row">
            <input v-model="password" type="password" autocomplete="current-password" required autofocus />
            <NeonButton type="button" variant="ghost" @click="confirming = null">Cancel</NeonButton>
            <NeonButton type="submit" variant="primary" :loading="busy">Confirm</NeonButton>
          </div>
        </form>

        <div v-if="error" class="form-error">⚠ {{ error }}</div>
        <div class="actions">
          <NeonButton v-if="canPasskey" variant="secondary" @click="adding = 'passkey'">
            <Plus :size="13" /> {{ thinClient ? 'Add security key' : 'Add passkey' }}
          </NeonButton>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { KeyRound, ListOrdered, Plus, Smartphone } from 'lucide-vue-next'
import NeonButton from './NeonButton.vue'
import LoadingSpinner from './LoadingSpinner.vue'
import RecoveryCodesBox from './RecoveryCodesBox.vue'
import TwoFactorSetup from './TwoFactorSetup.vue'
import { authApi } from '@/api/auth'
import { passkeysAvailable } from '@/utils/webauthn'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { EnrolResult, TwoFactorStatus } from '@/types'

defineProps<{ username?: string; thinClient?: boolean }>()

const ui = useUiStore()
const status = ref<TwoFactorStatus | null>(null)
const adding = ref<'totp' | 'passkey' | null>(null)
const newCodes = ref<string[] | null>(null)
const confirming = ref<{ kind: 'totp' | 'passkey' | 'codes'; id?: number; name?: string } | null>(null)
const password = ref('')
const busy = ref(false)
const error = ref('')
const auth = useAuthStore()
const canPasskey = computed(() => passkeysAvailable() && !!auth.config?.passkeys)

const confirmLabel = computed(() => {
  const c = confirming.value
  if (!c) return ''
  if (c.kind === 'totp') return 'Remove the authenticator app'
  if (c.kind === 'passkey') return `Remove “${c.name}”`
  return 'Replace your recovery codes (the old ones stop working)'
})

function fmt(ts: string) {
  return new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z').toLocaleDateString()
}

async function load() {
  try {
    status.value = await authApi.twoFactor.status()
  } catch (e: any) {
    error.value = e.message
  }
}

function ask(kind: 'totp' | 'passkey' | 'codes', id?: number, name?: string) {
  confirming.value = { kind, id, name }
  password.value = ''
  error.value = ''
}

async function runConfirmed() {
  const c = confirming.value
  if (!c) return
  busy.value = true
  error.value = ''
  try {
    if (c.kind === 'totp') await authApi.twoFactor.totpDisable(password.value)
    else if (c.kind === 'passkey' && c.id !== undefined) await authApi.twoFactor.passkeyDelete(c.id, password.value)
    else newCodes.value = (await authApi.twoFactor.recoveryCodes(password.value)).recovery_codes
    confirming.value = null
    if (c.kind !== 'codes') ui.toast('Removed', 'success')
    await load()
  } catch (e: any) {
    error.value = e.message
  } finally {
    busy.value = false
    password.value = ''
  }
}

async function afterAdd(_res: EnrolResult) {
  adding.value = null
  ui.toast('Two-factor sign-in updated', 'success')
  await load()
}

onMounted(load)
</script>

<style scoped>
.panel {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 24px; min-width: 0; display: flex; flex-direction: column; gap: 14px;
}
.panel h3 { font-family: var(--font-display); font-size: 13px; font-weight: 700; letter-spacing: 2px; color: var(--text); margin: 0 0 4px; }
.hint { color: var(--text-muted); font-size: 13px; line-height: 1.5; margin: 0; }
.hint strong { color: var(--amber); font-weight: 600; }
.items { list-style: none; margin: 0; padding: 0; }
.items li { display: flex; align-items: center; gap: 12px; padding: 10px 4px; border-top: 1px solid var(--border); }
.items li:last-child { border-bottom: 1px solid var(--border); }
.ic { color: var(--accent); flex-shrink: 0; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.name { font-size: 14px; color: var(--text); overflow-wrap: anywhere; }
.sub { font-size: 12px; color: var(--text-muted); }
.sub.on { color: var(--green); }
.sub.warn { color: var(--amber); }
.confirm { display: flex; flex-direction: column; gap: 6px; }
.confirm label { font-size: 12px; color: var(--text-muted); }
.confirm-row { display: flex; gap: 8px; align-items: center; }
.confirm-row input { flex: 1; min-width: 0; }
.actions { display: flex; justify-content: flex-end; }
.form-error { color: var(--red); font-size: 13px; }
</style>
