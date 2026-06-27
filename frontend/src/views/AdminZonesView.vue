<template>
  <AppShell>
    <div class="page-header">
      <h2>// ZONES</h2>
      <NeonButton variant="primary" @click="showCreate = true"><Plus :size="14" /> Add Zone</NeonButton>
    </div>
    <p class="hint">
      Zones are remote nodes that run workspace containers in their own network
      segment. The control plane dials each zone over mutual TLS. Zone
      <strong>Local</strong> is this control plane's own Docker daemon.
    </p>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Endpoint</th>
            <th>Workspaces</th>
            <th>Last Seen</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="z in zones" :key="z.id">
            <td>{{ z.name }}</td>
            <td><span class="badge" :class="z.status">{{ z.status }}</span></td>
            <td>{{ z.endpoint_host ? `${z.endpoint_host}:${z.endpoint_port}` : '—' }}</td>
            <td>{{ z.workspace_count }}</td>
            <td>{{ z.last_seen_at ? formatDate(z.last_seen_at) : '—' }}</td>
            <td class="actions">
              <NeonButton v-if="z.id !== 0" variant="ghost" @click="enroll(z)">
                <KeyRound :size="13" /> Enroll
              </NeonButton>
              <NeonButton
                v-if="z.id !== 0"
                variant="danger"
                :disabled="z.workspace_count > 0"
                :title="z.workspace_count > 0 ? 'Migrate or delete its workspaces first' : ''"
                @click="confirmDelete(z)"
              ><Trash2 :size="13" /> Delete</NeonButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Create -->
    <BaseModal v-model="showCreate" title="Add Zone">
      <form class="form" @submit.prevent="handleCreate">
        <label>Name<input v-model="form.name" required placeholder="LAN" /></label>
        <label>
          Endpoint host
          <input v-model="form.endpoint_host" placeholder="10.0.0.5 or agent.lan" />
          <small>The address the control plane will dial. Leave blank to set later.</small>
        </label>
        <label>mTLS port<input v-model.number="form.endpoint_port" type="number" /><small>The single port the control plane dials (streams, agent API, and Docker).</small></label>
        <NeonButton variant="primary" type="submit">Create</NeonButton>
      </form>
    </BaseModal>

    <!-- Enrollment install command -->
    <BaseModal v-model="showEnroll" title="Enroll Zone" width="640px">
      <p class="hint">
        Run this one-liner on the new node (as root). It installs Docker, enrolls
        over mTLS, and starts the agent. The token is single-use and expires.
      </p>
      <pre class="install">{{ enrollCmd }}</pre>
      <NeonButton variant="ghost" @click="copyCmd"><Copy :size="13" /> Copy</NeonButton>
    </BaseModal>

    <ConfirmModal
      v-model="showConfirm"
      title="Delete Zone"
      :message="`Delete zone '${deleteTarget?.name}'?`"
      confirm-label="Delete"
      :loading="deleting"
      @confirm="handleDelete"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import BaseModal from '@/components/BaseModal.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { Plus, Trash2, KeyRound, Copy } from 'lucide-vue-next'
import { zonesApi } from '@/api/zones'
import { useUiStore } from '@/stores/ui'
import type { Zone } from '@/types'

const zones = ref<Zone[]>([])
const ui = useUiStore()
const showCreate = ref(false)
const showEnroll = ref(false)
const showConfirm = ref(false)
const enrollCmd = ref('')
const deleteTarget = ref<Zone | null>(null)
const deleting = ref(false)
const form = ref({ name: '', endpoint_host: '', endpoint_port: 8443 })

onMounted(load)
async function load() { zones.value = await zonesApi.list() }
function formatDate(d: string) { return new Date(d).toLocaleString() }

async function handleCreate() {
  try {
    await zonesApi.create({
      name: form.value.name,
      endpoint_host: form.value.endpoint_host || undefined,
      endpoint_port: form.value.endpoint_port,
    })
    showCreate.value = false
    form.value = { name: '', endpoint_host: '', endpoint_port: 8443 }
    await load()
    ui.toast('Zone created', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
}

async function enroll(z: Zone) {
  try {
    const res = await zonesApi.enrollToken(z.id)
    enrollCmd.value = res.install_command
    showEnroll.value = true
    await load()
  } catch (e: any) { ui.toast(e.message, 'error') }
}

function copyCmd() {
  navigator.clipboard?.writeText(enrollCmd.value)
  ui.toast('Copied', 'success')
}

function confirmDelete(z: Zone) { deleteTarget.value = z; showConfirm.value = true }
async function handleDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await zonesApi.remove(deleteTarget.value.id)
    showConfirm.value = false
    await load()
    ui.toast('Zone deleted', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { deleting.value = false }
}
</script>

<style scoped>
@import '@/styles/tables.css';
.hint {
  font-family: var(--font-mono); font-size: 11px; line-height: 1.5;
  color: var(--text-muted); margin: 0 0 16px; max-width: 70ch;
}
.badge {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: 1px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 1px 6px; color: var(--text-muted); text-transform: uppercase;
}
.badge.enrolled { color: var(--accent); border-color: var(--accent); }
.badge.offline, .badge.error { color: #ff6b6b; border-color: #ff6b6b; }
.form { display: flex; flex-direction: column; gap: 12px; }
.form label { display: flex; flex-direction: column; gap: 4px; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }
.form input { padding: 6px 8px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text); font-family: var(--font-mono); }
.form small { color: var(--text-muted); font-size: 10px; }
.row { display: flex; gap: 12px; }
.row label { flex: 1; }
.install {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 10px; font-family: var(--font-mono); font-size: 11px; color: var(--text);
  white-space: pre-wrap; word-break: break-all; margin: 0 0 12px;
}
</style>
