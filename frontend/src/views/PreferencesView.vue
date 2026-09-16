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
        <LoadingSpinner v-if="tsLoading" block />
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

      <section class="panel">
        <h3>// GLUETUN VPN</h3>
        <LoadingSpinner v-if="gLoading" block />
        <form v-else class="form" @submit.prevent="handleGluetun">
          <label class="checkbox-row">
            <input type="checkbox" v-model="g.enabled" />
            <span>Enabled</span>
          </label>
          <div class="form-group">
            <label>// vpn type</label>
            <select v-model="g.vpn_type">
              <option value="openvpn">OpenVPN</option>
              <option value="wireguard">WireGuard</option>
            </select>
          </div>
          <div class="form-group">
            <label>// config file ({{ g.vpn_type === 'wireguard' ? '.conf' : '.ovpn' }})</label>
            <input type="file" accept=".ovpn,.conf,.txt" @change="onConfigFile" />
            <p class="hint">
              <template v-if="gFilename">Stored: <code>{{ gFilename }}</code> — upload to replace.</template>
              <template v-else>Upload your VPN config. Stored <strong>encrypted</strong> at rest (it may contain credentials).</template>
            </p>
          </div>
          <div v-if="g.vpn_type === 'wireguard'" class="form-group">
            <label>// private key override (optional)</label>
            <input
              v-model="g.wireguard_private_key"
              type="password"
              :placeholder="hasWgKey ? 'configured ✓ (leave blank to keep)' : 'overrides PrivateKey in the config'"
              autocomplete="off"
            />
          </div>
          <template v-else>
            <div class="form-group">
              <label>// openvpn username override (optional)</label>
              <input
                v-model="g.openvpn_user"
                type="text"
                :placeholder="hasOvpnUser ? 'configured ✓ (leave blank to keep)' : 'overrides config'"
                autocomplete="off"
              />
            </div>
            <div class="form-group">
              <label>// openvpn password override (optional)</label>
              <input
                v-model="g.openvpn_password"
                type="password"
                :placeholder="hasOvpnPass ? 'configured ✓ (leave blank to keep)' : 'overrides config'"
                autocomplete="off"
              />
            </div>
          </template>
          <p class="hint">
            Direct secrets override the matching values inside the config file. Pick
            "Route through Gluetun" per workspace at launch.
          </p>
          <div v-if="gError" class="form-error">⚠ {{ gError }}</div>
          <div class="form-actions">
            <NeonButton type="submit" variant="primary" :loading="gSaving">Save Gluetun</NeonButton>
          </div>
        </form>
      </section>

      <section class="panel">
        <h3>// SSH KEY</h3>
        <LoadingSpinner v-if="sshLoading" block />
        <div v-else class="form">
          <p class="hint">
            Your account SSH key is copied into each workspace's <code>~/.ssh</code> at
            launch (toggle off per workspace in its options). The private key is stored
            <strong>encrypted</strong> at rest and never shown.
          </p>

          <template v-if="ssh.has_key">
            <div class="form-group">
              <label>// public key ({{ ssh.key_type }})</label>
              <textarea :value="ssh.public_key || ''" readonly rows="3" class="mono" @focus="selectAll" />
              <p class="hint">Fingerprint: <code>{{ ssh.fingerprint }}</code></p>
            </div>
            <div v-if="sshError" class="form-error">⚠ {{ sshError }}</div>
            <div class="form-actions ssh-actions">
              <NeonButton variant="ghost" @click="copyPublic">Copy public key</NeonButton>
              <NeonButton variant="secondary" :loading="sshSaving" @click="generate">Regenerate</NeonButton>
              <NeonButton variant="danger" :loading="sshSaving" @click="removeKey">Remove</NeonButton>
            </div>
          </template>

          <template v-else>
            <div class="form-group">
              <label>// upload private key</label>
              <input type="file" @change="onKeyFile" />
              <textarea
                v-model="uploadText"
                rows="4"
                class="mono"
                placeholder="Paste an unencrypted private key, or pick a file above"
                autocomplete="off"
                spellcheck="false"
              />
              <p class="hint">
                Unencrypted keys only (no passphrase). The matching public key is derived
                automatically.
              </p>
            </div>
            <div v-if="sshError" class="form-error">⚠ {{ sshError }}</div>
            <div class="form-actions ssh-actions">
              <NeonButton variant="secondary" :loading="sshSaving" :disabled="!uploadText.trim()" @click="upload">
                Upload key
              </NeonButton>
              <NeonButton variant="primary" :loading="sshSaving" @click="generate">Generate new key</NeonButton>
            </div>
          </template>
        </div>
      </section>

      <section v-if="extension.available" class="panel">
        <h3>// BROWSER EXTENSION</h3>
        <div class="form">
          <p class="hint">
            <strong>Open in Cove</strong> adds a right-click action to your own browser:
            send any link to a fresh Cove browser workspace instead of opening it here.
            You pick the browser, the network route (direct, VPN or Tailscale) and the
            start-up options, and the page loads in a container.
          </p>
          <p class="hint">
            <strong>For Chrome-based browsers</strong> — Chrome, Edge, Brave, Vivaldi,
            Opera, Helium and other Chromium forks. There is no Firefox build.
          </p>

          <div class="ext-actions">
            <NeonButton variant="primary" @click="downloadExtension">
              <Download :size="14" /> Download{{ extension.version ? ` v${extension.version}` : '' }}
            </NeonButton>
            <span class="hint">{{ extSize }} · unpacked extension (zip)</span>
          </div>

          <ol class="steps-list">
            <li>Unzip it somewhere it can stay — your browser loads the extension from that folder every time it starts.</li>
            <li>Open <code>chrome://extensions</code> (Edge <code>edge://extensions</code>, Brave <code>brave://extensions</code>…).</li>
            <li>Turn on <strong>Developer mode</strong>.</li>
            <li>Click <strong>Load unpacked</strong> and pick the unzipped folder — the one holding <code>manifest.json</code>.</li>
            <li>Open the extension's <strong>options</strong> and enter this Cove address: <code>{{ coveOrigin }}</code></li>
          </ol>
          <p class="hint">
            To update later, unzip the new version over the same folder and press
            <strong>Reload</strong> on the extension's card.
          </p>
        </div>
      </section>
    </div>

    <ConfirmModal
      v-model="showSshReplace"
      title="Replace SSH Key"
      message="Replace your current SSH key with a new one? Existing workspaces keep the old key until they next launch."
      confirm-label="Replace"
      @confirm="doGenerate"
    />
    <ConfirmModal
      v-model="showSshRemove"
      title="Remove SSH Key"
      message="Remove your SSH key? New workspaces will launch without it."
      confirm-label="Remove"
      @confirm="doRemove"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import NeonButton from '@/components/NeonButton.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { Download } from 'lucide-vue-next'
import { authApi } from '@/api/auth'
import { extensionApi } from '@/api/extension'
import { usersApi, type GluetunUpdate } from '@/api/users'
import type { ExtensionInfo, SshKeyConfig } from '@/types'
import { useUiStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'

const ui = useUiStore()
const auth = useAuthStore()

// The browser extension this deployment ships, if any (a build may omit it).
const extension = ref<ExtensionInfo>({ available: false, version: null, size_bytes: 0, filename: '' })
const coveOrigin = window.location.origin
const extSize = computed(() => {
  const kb = extension.value.size_bytes / 1024
  return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.round(kb)} KB`
})

async function downloadExtension() {
  try {
    await extensionApi.download(extension.value.filename)
  } catch (e: any) {
    ui.toast(e?.message || 'Download failed', 'error')
  }
}

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
  // Best-effort: a deployment without the extension just doesn't show the panel.
  extensionApi.info().then(info => { extension.value = info }).catch(() => {})

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

  try {
    const cfg = await usersApi.getGluetun()
    g.enabled = cfg.enabled
    g.vpn_type = cfg.vpn_type
    gFilename.value = cfg.config_filename
    hasWgKey.value = cfg.has_wireguard_private_key
    hasOvpnUser.value = cfg.has_openvpn_user
    hasOvpnPass.value = cfg.has_openvpn_password
  } catch (e: any) {
    gError.value = e.message
  } finally {
    gLoading.value = false
  }

  try {
    ssh.value = await usersApi.getSshKey()
  } catch (e: any) {
    sshError.value = e.message
  } finally {
    sshLoading.value = false
  }
})

// --- Gluetun ---
const gLoading = ref(true)
const gSaving = ref(false)
const gError = ref('')
const gFilename = ref<string | null>(null)
const hasWgKey = ref(false)
const hasOvpnUser = ref(false)
const hasOvpnPass = ref(false)
const gConfigContent = ref<string | null>(null) // text of a newly-uploaded file
const g = reactive({
  enabled: false,
  vpn_type: 'openvpn' as 'openvpn' | 'wireguard',
  wireguard_private_key: '',
  openvpn_user: '',
  openvpn_password: '',
})

async function onConfigFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  gConfigContent.value = await file.text()
  gFilename.value = file.name
}

async function handleGluetun() {
  gError.value = ''
  gSaving.value = true
  try {
    const payload: GluetunUpdate = { enabled: g.enabled, vpn_type: g.vpn_type }
    if (gConfigContent.value !== null) {
      payload.config_file = gConfigContent.value
      payload.config_filename = gFilename.value
    }
    if (g.wireguard_private_key) payload.wireguard_private_key = g.wireguard_private_key
    if (g.openvpn_user) payload.openvpn_user = g.openvpn_user
    if (g.openvpn_password) payload.openvpn_password = g.openvpn_password
    const cfg = await usersApi.updateGluetun(payload)
    gFilename.value = cfg.config_filename
    hasWgKey.value = cfg.has_wireguard_private_key
    hasOvpnUser.value = cfg.has_openvpn_user
    hasOvpnPass.value = cfg.has_openvpn_password
    g.wireguard_private_key = ''
    g.openvpn_user = ''
    g.openvpn_password = ''
    gConfigContent.value = null
    ui.toast('Gluetun settings saved', 'success')
  } catch (e: any) {
    gError.value = e.message
  } finally {
    gSaving.value = false
  }
}

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

// --- SSH key ---
const sshLoading = ref(true)
const sshSaving = ref(false)
const sshError = ref('')
const uploadText = ref('')
const showSshReplace = ref(false)
const showSshRemove = ref(false)
const ssh = ref<SshKeyConfig>({
  has_key: false,
  public_key: null,
  key_type: null,
  fingerprint: null,
})

function selectAll(e: FocusEvent) {
  (e.target as HTMLTextAreaElement).select()
}

async function copyPublic() {
  if (!ssh.value.public_key) return
  try {
    await navigator.clipboard.writeText(ssh.value.public_key)
    ui.toast('Public key copied', 'success')
  } catch {
    ui.toast('Copy failed — select and copy manually', 'error')
  }
}

async function onKeyFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  uploadText.value = await file.text()
}

async function upload() {
  if (!uploadText.value.trim()) return
  sshError.value = ''
  sshSaving.value = true
  try {
    ssh.value = await usersApi.uploadSshKey(uploadText.value)
    uploadText.value = ''
    ui.toast('SSH key saved', 'success')
  } catch (e: any) {
    sshError.value = e.message
  } finally {
    sshSaving.value = false
  }
}

// Replacing an existing key is destructive, so route it through the themed
// ConfirmModal (consistent with the rest of the app); a first-time generate with
// no existing key needs no confirmation.
function generate() {
  if (ssh.value.has_key) { showSshReplace.value = true; return }
  doGenerate()
}

async function doGenerate() {
  showSshReplace.value = false
  sshError.value = ''
  sshSaving.value = true
  try {
    ssh.value = await usersApi.generateSshKey()
    uploadText.value = ''
    ui.toast('New SSH key generated', 'success')
  } catch (e: any) {
    sshError.value = e.message
  } finally {
    sshSaving.value = false
  }
}

function removeKey() {
  showSshRemove.value = true
}

async function doRemove() {
  showSshRemove.value = false
  sshError.value = ''
  sshSaving.value = true
  try {
    ssh.value = await usersApi.deleteSshKey()
    ui.toast('SSH key removed', 'success')
  } catch (e: any) {
    sshError.value = e.message
  } finally {
    sshSaving.value = false
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
.ext-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.steps-list {
  margin: 4px 0 0; padding-left: 20px;
  display: flex; flex-direction: column; gap: 6px;
  font-size: 12px; line-height: 1.55; color: var(--text-muted);
}
.steps-list code { font-family: var(--font-mono); font-size: 11px; color: var(--accent); }
.hint {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  margin: 0;
}
.mono {
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.4;
  word-break: break-all;
  resize: vertical;
}
.ssh-actions { gap: 10px; flex-wrap: wrap; }
</style>
