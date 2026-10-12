<template>
  <AppShell>
    <div class="page-header">
      <h2>// PREFERENCES</h2>
    </div>

    <div class="panels">
      <section v-if="isLocalUser" class="panel">
        <h3>CHANGE PASSWORD</h3>
        <form class="form" @submit.prevent="handlePassword">
          <div class="form-group">
            <label>Current password</label>
            <input v-model="pw.current" type="password" required autocomplete="current-password" />
          </div>
          <div class="form-group">
            <label>New password (min 8 characters)</label>
            <input v-model="pw.next" type="password" required minlength="8" autocomplete="new-password" />
          </div>
          <div class="form-group">
            <label>Confirm new password</label>
            <input v-model="pw.confirm" type="password" required autocomplete="new-password" />
          </div>
          <div v-if="pwError" class="form-error">⚠ {{ pwError }}</div>
          <div class="form-actions">
            <NeonButton type="submit" variant="primary" :loading="pwSaving">Update Password</NeonButton>
          </div>
        </form>
      </section>

      <section id="vpn" class="panel">
        <div class="panel-head">
          <h3>VPN PROFILES</h3>
          <NeonButton variant="ghost" @click="editVpn(null)"><Plus :size="13" /> Add</NeonButton>
        </div>
        <LoadingSpinner v-if="vpnLoading" block />
        <template v-else>
          <p v-if="vpnError" class="form-error">⚠ {{ vpnError }}</p>
          <ul v-if="vpnProfiles.length" class="profiles">
            <li v-for="p in vpnProfiles" :key="p.id" class="profile" @contextmenu="vpnMenus.get(p.id)?.openAt($event)">
              <component :is="p.problem ? AlertTriangle : ShieldCheck" :size="16" class="p-icon" :class="{ bad: !!p.problem }" />
              <div class="p-main">
                <div class="p-name">{{ p.name }}</div>
                <div class="p-sub">{{ vpnSummary(p) }}</div>
                <div class="p-state">
                  <span v-if="p.problem" class="st bad">Can't connect: {{ p.problem }}</span>
                  <span v-else-if="p.active_in" class="st on">● Connected in {{ p.active_in }}</span>
                  <span v-else class="st">Ready</span>
                  <span v-if="p.used_by.length" class="st" :title="p.used_by.join(', ')">
                    · used by {{ p.used_by.length === 1 ? p.used_by[0] : `${p.used_by.length} workspaces` }}
                  </span>
                </div>
              </div>
              <RowMenu :ref="(el: unknown) => setMenu(vpnMenus, p.id, el)" :items="vpnMenu(p)" :label="`Actions for ${p.name}`" />
            </li>
          </ul>
          <p v-else class="empty">No VPN profiles yet. Add one to route workspaces through a VPN.</p>
          <p class="hint">
            Each profile is one connection: two keys for the same provider are two profiles, and both
            can be connected at once. You pick the profile per workspace when launching or editing it.
          </p>
        </template>
      </section>

      <section id="tailscale" class="panel">
        <div class="panel-head">
          <h3>TAILSCALE PROFILES</h3>
          <NeonButton variant="ghost" @click="editTs(null)"><Plus :size="13" /> Add</NeonButton>
        </div>
        <LoadingSpinner v-if="tsLoading" block />
        <template v-else>
          <p v-if="tsError" class="form-error">⚠ {{ tsError }}</p>
          <ul v-if="tsProfiles.length" class="profiles">
            <li v-for="p in tsProfiles" :key="p.id" class="profile" @contextmenu="tsMenus.get(p.id)?.openAt($event)">
              <component :is="p.problem ? AlertTriangle : Network" :size="16" class="p-icon" :class="{ bad: !!p.problem }" />
              <div class="p-main">
                <div class="p-name">{{ p.name }}</div>
                <div class="p-sub">{{ p.login_server ? host(p.login_server) : 'Tailscale' }}</div>
                <div class="p-state">
                  <span v-if="p.problem" class="st bad">Can't connect: {{ p.problem }}</span>
                  <span v-else class="st">Ready</span>
                  <span v-if="p.used_by.length" class="st" :title="p.used_by.join(', ')">
                    · used by {{ p.used_by.length === 1 ? p.used_by[0] : `${p.used_by.length} workspaces` }}
                  </span>
                </div>
              </div>
              <RowMenu :ref="(el: unknown) => setMenu(tsMenus, p.id, el)" :items="tsMenu(p)" :label="`Actions for ${p.name}`" />
            </li>
          </ul>
          <p v-else class="empty">No Tailscale profiles yet. Add one with an auth key to put workspaces on a tailnet.</p>
          <p class="hint">
            Use several to join different tailnets or Headscale servers, or to keep tagged and untagged
            keys apart. Exit node and route options are set per workspace.
          </p>
        </template>
      </section>

      <section class="panel">
        <h3>SSH KEY</h3>
        <LoadingSpinner v-if="sshLoading" block />
        <div v-else class="form">
          <p class="hint">
            Your account SSH key is copied into each workspace's <code>~/.ssh</code> at
            launch (toggle off per workspace in its options). The private key is stored
            <strong>encrypted</strong> at rest and never shown.
          </p>

          <template v-if="ssh.has_key">
            <div class="form-group">
              <label>Public key ({{ ssh.key_type }})</label>
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
              <label>Upload private key</label>
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

    </div>

    <VpnProfileModal v-model="showVpnModal" :profile="vpnEditing" :providers="providers" @saved="loadVpn" />
    <TailscaleProfileModal v-model="showTsModal" :profile="tsEditing" @saved="loadTs" />
    <ConfirmModal
      v-model="showDeleteProfile"
      :title="deleting?.kind === 'vpn' ? 'Delete VPN profile' : 'Delete Tailscale profile'"
      :message="`Delete “${deleting?.name}”? Its stored key${deleting?.kind === 'vpn' ? ' / config' : ''} is removed for good.`"
      confirm-label="Delete"
      @confirm="doDeleteProfile"
    />
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
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import AppShell from '@/components/AppShell.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import NeonButton from '@/components/NeonButton.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import RowMenu, { type RowMenuItem } from '@/components/RowMenu.vue'
import VpnProfileModal from '@/components/VpnProfileModal.vue'
import TailscaleProfileModal from '@/components/TailscaleProfileModal.vue'
import { AlertTriangle, Network, Pencil, Plus, ShieldCheck, Trash2 } from 'lucide-vue-next'
import { authApi } from '@/api/auth'
import { usersApi } from '@/api/users'
import type { SshKeyConfig, TailscaleProfile, VpnProfile, VpnProvider } from '@/types'
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

// --- VPN / Tailscale profiles ---
type MenuHandle = { openAt: (e: MouseEvent) => void }
const vpnMenus = new Map<number, MenuHandle>()
const tsMenus = new Map<number, MenuHandle>()
function setMenu(map: Map<number, MenuHandle>, id: number, el: unknown) {
  if (el) map.set(id, el as MenuHandle)
  else map.delete(id)
}

const vpnLoading = ref(true)
const vpnError = ref('')
const vpnProfiles = ref<VpnProfile[]>([])
const providers = ref<VpnProvider[]>([])
const showVpnModal = ref(false)
const vpnEditing = ref<VpnProfile | null>(null)

const tsLoading = ref(true)
const tsError = ref('')
const tsProfiles = ref<TailscaleProfile[]>([])
const showTsModal = ref(false)
const tsEditing = ref<TailscaleProfile | null>(null)

const showDeleteProfile = ref(false)
const deleting = ref<{ kind: 'vpn' | 'ts'; id: number; name: string } | null>(null)

async function loadVpn() {
  try {
    vpnProfiles.value = await usersApi.listVpnProfiles()
    vpnError.value = ''
  } catch (e: any) {
    vpnError.value = e.message
  } finally {
    vpnLoading.value = false
  }
}
async function loadTs() {
  try {
    tsProfiles.value = await usersApi.listTailscaleProfiles()
    tsError.value = ''
  } catch (e: any) {
    tsError.value = e.message
  } finally {
    tsLoading.value = false
  }
}

function editVpn(p: VpnProfile | null) {
  vpnEditing.value = p
  showVpnModal.value = true
}
function editTs(p: TailscaleProfile | null) {
  tsEditing.value = p
  showTsModal.value = true
}

function inUseTitle(used: string[]) {
  return used.length ? `In use by ${used.join(', ')} — switch them to another profile first` : undefined
}
function vpnMenu(p: VpnProfile): RowMenuItem[] {
  return [
    { key: 'edit', label: 'Edit', icon: Pencil, action: () => editVpn(p) },
    {
      key: 'delete', label: 'Delete…', icon: Trash2, danger: true, separator: true,
      disabled: p.used_by.length > 0, title: inUseTitle(p.used_by),
      action: () => askDelete('vpn', p.id, p.name),
    },
  ]
}
function tsMenu(p: TailscaleProfile): RowMenuItem[] {
  return [
    { key: 'edit', label: 'Edit', icon: Pencil, action: () => editTs(p) },
    {
      key: 'delete', label: 'Delete…', icon: Trash2, danger: true, separator: true,
      disabled: p.used_by.length > 0, title: inUseTitle(p.used_by),
      action: () => askDelete('ts', p.id, p.name),
    },
  ]
}
function askDelete(kind: 'vpn' | 'ts', id: number, name: string) {
  deleting.value = { kind, id, name }
  showDeleteProfile.value = true
}
async function doDeleteProfile() {
  const d = deleting.value
  showDeleteProfile.value = false
  if (!d) return
  try {
    if (d.kind === 'vpn') {
      await usersApi.deleteVpnProfile(d.id)
      await loadVpn()
    } else {
      await usersApi.deleteTailscaleProfile(d.id)
      await loadTs()
    }
    ui.toast(`Deleted “${d.name}”`, 'success')
  } catch (e: any) {
    ui.toast(e?.message || 'Delete failed', 'error')
  }
}

function host(url: string) {
  try { return new URL(url).host } catch { return url }
}
function vpnSummary(p: VpnProfile) {
  const kind = p.vpn_type === 'wireguard' ? 'WireGuard' : 'OpenVPN'
  if (p.mode === 'provider') {
    const where = [p.server_countries, p.server_cities, p.server_hostnames].filter(Boolean).join(' / ')
    return [p.provider_label ?? p.provider, kind, where || 'any server'].join(' · ')
  }
  return [kind, p.config_filename ?? 'config file'].join(' · ')
}

onMounted(async () => {
  usersApi.listVpnProviders().then(p => { providers.value = p }).catch(() => {})
  await Promise.all([loadVpn(), loadTs()])
  // Launch / edit forms link here as #vpn or #tailscale (in a new tab); the
  // panels only exist once rendered, so the browser's own jump misses them.
  if (location.hash) {
    await nextTick()
    document.querySelector(location.hash)?.scrollIntoView({ block: 'start' })
  }
  try {
    ssh.value = await usersApi.getSshKey()
  } catch (e: any) {
    sshError.value = e.message
  } finally {
    sshLoading.value = false
  }
})

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

/* Responsive grid: fills the width instead of fixed 420px cards that left a
   quarter of a wide screen empty. */
.panels {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 380px), 1fr));
  gap: 24px;
  align-items: start;
}
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  min-width: 0;
}
/* Section titles: plain, so the page title stays the one glowing heading. */
.panel h3 {
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--text);
  margin-bottom: 18px;
}
.form { display: flex; flex-direction: column; gap: 14px; }
.form-actions { display: flex; justify-content: flex-end; }
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 13px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
  margin-bottom: 0;
}
.checkbox-row input { width: auto; margin: 0; }
.hint {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  margin: 0;
}
.mono {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.4;
  word-break: break-all;
  resize: vertical;
}
.ssh-actions { gap: 10px; flex-wrap: wrap; }
.panel-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.panel-head h3 { margin-bottom: 0; }
.profiles { list-style: none; margin: 0 0 14px; padding: 0; display: flex; flex-direction: column; }
.profile {
  display: flex; align-items: flex-start; gap: 12px; padding: 12px 4px;
  border-top: 1px solid var(--border);
}
.profile:last-child { border-bottom: 1px solid var(--border); }
.p-icon { flex-shrink: 0; margin-top: 2px; color: var(--accent); }
.p-icon.bad { color: var(--amber); }
.p-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.p-name { font-size: 14px; color: var(--text); overflow-wrap: anywhere; }
.p-sub { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); overflow-wrap: anywhere; }
.p-state { font-size: 12px; color: var(--text-muted); display: flex; flex-wrap: wrap; gap: 0 6px; }
.st.on { color: var(--green); }
.st.bad { color: var(--amber); }
.empty { font-size: 13px; color: var(--text-muted); margin: 0 0 14px; }
.form-error { color: var(--red); font-size: 13px; margin: 0 0 10px; }
</style>
