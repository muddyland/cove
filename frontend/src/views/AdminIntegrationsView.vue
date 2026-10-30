<template>
  <AppShell>
    <div class="page-header">
      <h2>// INTEGRATIONS</h2>
      <div class="header-actions">
        <NeonButton variant="ghost" :loading="downloading" @click="downloadComponent">
          <Download :size="14" /> Home Assistant integration
        </NeonButton>
        <NeonButton variant="primary" @click="openCreate"><Plus :size="14" /> New Token</NeonButton>
      </div>
    </div>
    <p class="hint">
      Tokens for Home Assistant. A token reads Cove's status: users, workspaces,
      zones and every thin client. With <strong>Control</strong> it can also see
      thin client screenshots, switch their screens, set their volume, sign them
      out, restart their browser and reboot them. A token is revoked when the admin
      who created it is deleted or loses admin. It works only on Cove's integration API, never as a sign-in,
      so it can't reach workspaces, files or administration. Home Assistant calls
      Cove over the same HTTPS address you use; nothing new is exposed.
    </p>
    <p class="hint">
      To install, download the integration, unzip it into Home Assistant's config
      folder (it adds <code>custom_components/cove</code>), restart Home Assistant,
      then add <strong>Cove</strong> under Settings → Devices &amp; services.
    </p>

    <LoadingSpinner v-if="loading" block />
    <p v-else-if="!tokens.length" class="empty">No tokens yet.</p>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Access</th>
            <th>Token</th>
            <th>Created</th>
            <th>Last used</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tokens" :key="t.id">
            <td>{{ t.name }}</td>
            <td><span class="badge" :class="t.scope">{{ t.scope }}</span></td>
            <td class="mono">{{ t.prefix }}…</td>
            <td :title="absoluteTime(t.created_at)">
              {{ relativeTime(t.created_at) }}<span v-if="t.created_by" class="muted"> · {{ t.created_by }}</span>
            </td>
            <td :title="t.last_used_at ? absoluteTime(t.last_used_at) : ''">
              <template v-if="t.last_used_at">
                {{ relativeTime(t.last_used_at) }}<span v-if="t.last_used_ip" class="muted"> · {{ t.last_used_ip }}</span>
              </template>
              <span v-else class="muted">never</span>
            </td>
            <td class="actions">
              <NeonButton variant="danger" @click="confirmRevoke(t)"><Trash2 :size="13" /> Revoke</NeonButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <BaseModal v-model="showCreate" title="New Integration Token">
      <form class="form" @submit.prevent="handleCreate">
        <label>Name<input v-model="form.name" required maxlength="128" placeholder="Home Assistant" /></label>
        <label>
          Access
          <select v-model="form.scope">
            <option value="control">Control: status, plus thin client screenshots, screens, volume, sign-out, reboot</option>
            <option value="read">Read: status only</option>
          </select>
        </label>
        <NeonButton variant="primary" type="submit" :loading="creating">Create</NeonButton>
      </form>
    </BaseModal>

    <BaseModal v-model="showToken" title="Token Created" width="640px">
      <p class="hint">
        Copy it now: Cove shows it only once. In Home Assistant, add the
        <strong>Cove</strong> integration and paste it with this address:
      </p>
      <pre class="install">{{ origin }}</pre>
      <pre class="install">{{ created?.token }}</pre>
      <NeonButton variant="ghost" @click="copyToken"><Copy :size="13" /> Copy token</NeonButton>
    </BaseModal>

    <ConfirmModal
      v-model="showRevoke"
      title="Revoke Token"
      :message="`Revoke '${revokeTarget?.name}'? Whatever uses it stops working at once.`"
      confirm-label="Revoke"
      :loading="revoking"
      @confirm="handleRevoke"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppShell from '@/components/AppShell.vue'
import BaseModal from '@/components/BaseModal.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import NeonButton from '@/components/NeonButton.vue'
import { Copy, Download, Plus, Trash2 } from 'lucide-vue-next'
import { integrationsApi } from '@/api/integrations'
import { useUiStore } from '@/stores/ui'
import { absoluteTime, relativeTime } from '@/utils/workspaceStatus'
import type { IntegrationScope, IntegrationToken, IntegrationTokenCreated } from '@/types'

const ui = useUiStore()
const tokens = ref<IntegrationToken[]>([])
const loading = ref(true)
const origin = window.location.origin

const showCreate = ref(false)
const creating = ref(false)
const form = ref<{ name: string; scope: IntegrationScope }>({ name: 'Home Assistant', scope: 'control' })
const showToken = ref(false)
const created = ref<IntegrationTokenCreated | null>(null)

const downloading = ref(false)

const showRevoke = ref(false)
const revoking = ref(false)
const revokeTarget = ref<IntegrationToken | null>(null)

async function load() {
  try {
    tokens.value = await integrationsApi.list()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function downloadComponent() {
  downloading.value = true
  try {
    const blob = await integrationsApi.homeAssistantZip()
    if (!blob) return
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'cove-home-assistant.zip'
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    downloading.value = false
  }
}

function openCreate() {
  form.value = { name: 'Home Assistant', scope: 'control' }
  showCreate.value = true
}

async function handleCreate() {
  creating.value = true
  try {
    created.value = await integrationsApi.create(form.value.name.trim(), form.value.scope)
    showCreate.value = false
    showToken.value = true
    await load()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    creating.value = false
  }
}

function copyToken() {
  if (!created.value) return
  navigator.clipboard?.writeText(created.value.token)
  ui.toast('Copied', 'success')
}

function confirmRevoke(t: IntegrationToken) {
  revokeTarget.value = t
  showRevoke.value = true
}

async function handleRevoke() {
  if (!revokeTarget.value) return
  revoking.value = true
  try {
    await integrationsApi.revoke(revokeTarget.value.id)
    showRevoke.value = false
    ui.toast('Token revoked', 'success')
    await load()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    revoking.value = false
  }
}

onMounted(load)
</script>

<style scoped>
@import '@/styles/tables.css';
.hint {
  font-family: var(--font-mono); font-size: 12px; line-height: 1.5;
  color: var(--text-muted); margin: 0 0 16px; max-width: 74ch;
}
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.hint code { font-size: 11px; color: var(--text); }
.empty { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); }
.badge {
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 1px 6px; color: var(--text-muted); text-transform: uppercase;
}
.badge.control { color: var(--accent); border-color: var(--accent); }
.form { display: flex; flex-direction: column; gap: 12px; }
.form label { display: flex; flex-direction: column; gap: 4px; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }
.form input, .form select { padding: 6px 8px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text); font-family: var(--font-mono); }
.install {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 10px; font-family: var(--font-mono); font-size: 12px; color: var(--text);
  white-space: pre-wrap; word-break: break-all; margin: 0 0 12px;
}
</style>
