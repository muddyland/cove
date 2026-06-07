<template>
  <AppShell>
    <div class="page-header">
      <h2>// PREFERENCES</h2>
    </div>

    <div class="panels">
      <section v-if="isLocalUser" class="panel">
        <h3>// CHANGE PASSWORD</h3>
        <form class="form" @submit.prevent="handlePassword">
          <div class="form-group">
            <label>// current passkey</label>
            <input v-model="pw.current" type="password" required autocomplete="current-password" />
          </div>
          <div class="form-group">
            <label>// new passkey (min 8 chars)</label>
            <input v-model="pw.next" type="password" required minlength="8" autocomplete="new-password" />
          </div>
          <div class="form-group">
            <label>// confirm passkey</label>
            <input v-model="pw.confirm" type="password" required autocomplete="new-password" />
          </div>
          <div v-if="pwError" class="form-error">⚠ {{ pwError }}</div>
          <div class="form-actions">
            <NeonButton type="submit" variant="primary" :loading="pwSaving">Update Password</NeonButton>
          </div>
        </form>
      </section>

      <section class="panel">
        <h3>// TAILSCALE</h3>
        <div v-if="tsLoading" class="loading">Loading…</div>
        <form v-else class="form" @submit.prevent="handleTailscale">
          <label class="checkbox-row">
            <input type="checkbox" v-model="ts.enabled" />
            <span>Enabled</span>
          </label>
          <div class="form-group">
            <label>// auth key</label>
            <input
              v-model="ts.auth_key"
              type="password"
              :placeholder="hasAuthKey ? 'configured ✓ (leave blank to keep)' : 'tskey-…'"
              autocomplete="off"
            />
          </div>
          <div class="form-group">
            <label>// login server (optional)</label>
            <input v-model="ts.login_server" type="url" placeholder="https://login.tailscale.com" />
          </div>
          <p class="hint">
            Per-connection options (exit node, accept routes, accept DNS) are now chosen
            per workspace at launch time.
          </p>
          <div v-if="tsError" class="form-error">⚠ {{ tsError }}</div>
          <div class="form-actions">
            <NeonButton type="submit" variant="primary" :loading="tsSaving">Save Tailscale</NeonButton>
          </div>
        </form>
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import { authApi } from '@/api/auth'
import { usersApi } from '@/api/users'
import { useUiStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'

const ui = useUiStore()
const auth = useAuthStore()

// SSO (OIDC) accounts have no local password — hide the change-password panel.
const isLocalUser = computed(() => auth.user?.auth_provider === 'local')

// --- Change password ---
const pw = reactive({ current: '', next: '', confirm: '' })
const pwError = ref('')
const pwSaving = ref(false)

async function handlePassword() {
  pwError.value = ''
  if (pw.next.length < 8) {
    pwError.value = 'New password must be at least 8 characters'
    return
  }
  if (pw.next !== pw.confirm) {
    pwError.value = 'Passwords do not match'
    return
  }
  pwSaving.value = true
  try {
    await authApi.changePassword(pw.current, pw.next)
    ui.toast('Password updated', 'success')
    pw.current = ''
    pw.next = ''
    pw.confirm = ''
  } catch (e: any) {
    pwError.value = e.message
  } finally {
    pwSaving.value = false
  }
}

// --- Tailscale ---
const tsLoading = ref(true)
const tsSaving = ref(false)
const tsError = ref('')
const hasAuthKey = ref(false)
const ts = reactive({
  enabled: false,
  auth_key: '',
  login_server: '' as string,
})

onMounted(async () => {
  try {
    const cfg = await usersApi.getTailscale()
    ts.enabled = cfg.enabled
    ts.login_server = cfg.login_server ?? ''
    hasAuthKey.value = cfg.has_auth_key
  } catch (e: any) {
    tsError.value = e.message
  } finally {
    tsLoading.value = false
  }
})

async function handleTailscale() {
  tsError.value = ''
  tsSaving.value = true
  try {
    const payload: Record<string, unknown> = {
      enabled: ts.enabled,
      login_server: ts.login_server || null,
    }
    if (ts.auth_key) payload.auth_key = ts.auth_key
    const cfg = await usersApi.updateTailscale(payload)
    hasAuthKey.value = cfg.has_auth_key
    ts.auth_key = ''
    ui.toast('Tailscale settings saved', 'success')
  } catch (e: any) {
    tsError.value = e.message
  } finally {
    tsSaving.value = false
  }
}
</script>

<style scoped>
@import '@/styles/tables.css';

.panels { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  width: 420px;
  max-width: 100%;
}
.panel h3 {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--accent);
  text-shadow: var(--glow-sm);
  margin-bottom: 18px;
}
.form { display: flex; flex-direction: column; gap: 14px; }
.form-actions { display: flex; justify-content: flex-end; }
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
  margin-bottom: 0;
}
.checkbox-row input { width: auto; margin: 0; }
.loading { color: var(--text-muted); font-family: var(--font-mono); font-size: 12px; }
.hint {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  margin: 0;
}
</style>
