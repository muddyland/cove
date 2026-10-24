<template>
  <AppShell>
    <div class="page-header">
      <h2>// ZONES</h2>
      <NeonButton variant="primary" @click="showCreate = true"><Plus :size="14" /> Add Zone</NeonButton>
    </div>
    <p class="hint">
      Zones are remote hosts that run workspace containers in their own network
      segment. The control plane dials each zone over mutual TLS. Zone
      <strong>Local</strong> is this control plane's own Docker daemon.
    </p>

    <LoadingSpinner v-if="loading" block />
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Health</th>
            <th>Endpoint</th>
            <th>Workspaces</th>
            <th>Last Seen</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="z in zones" :key="z.id">
            <td>{{ z.name }}</td>
            <td><span class="badge" :class="z.status">{{ z.id === 0 ? 'this host' : z.status }}</span></td>
            <td>
              <span v-if="health[z.id] === undefined" class="health muted">checking…</span>
              <span v-else-if="health[z.id]?.ok" class="health ok" :title="`Docker ${health[z.id]?.docker_version ?? ''}`">
                <CheckCircle2 :size="13" /> {{ health[z.id]?.latency_ms }} ms
                <span class="muted">· {{ health[z.id]?.containers_running ?? '?' }} containers</span>
              </span>
              <span v-else class="health bad" :title="health[z.id]?.error || ''"><XCircle :size="13" /> {{ health[z.id]?.error === 'Not enrolled' ? 'not enrolled' : 'unreachable' }}</span>
            </td>
            <td>{{ z.endpoint_host ? `${z.endpoint_host}:${z.endpoint_port}` : z.id === 0 ? 'local Docker socket' : '—' }}</td>
            <td>{{ z.workspace_count }}</td>
            <td :title="z.last_seen_at ? formatDate(z.last_seen_at) : ''">
              {{ z.last_seen_at ? relativeTime(z.last_seen_at) : z.id === 0 ? 'n/a (local)' : 'never' }}
            </td>
            <td class="actions">
              <NeonButton
                v-if="z.id !== 0"
                variant="ghost"
                :loading="enrollingId === z.id"
                :disabled="enrollingId === z.id"
                @click="enroll(z)"
              >
                <KeyRound :size="13" /> Enroll
              </NeonButton>
              <NeonButton
                v-if="z.id !== 0 && z.status === 'enrolled'"
                variant="ghost"
                :loading="updatingId === z.id"
                @click="confirmUpdate(z)"
              ><ArrowUpCircle :size="13" /> Update</NeonButton>
              <NeonButton
                v-if="z.id !== 0"
                variant="ghost"
                @click="openSettings(z)"
              ><SlidersHorizontal :size="13" /> Settings</NeonButton>
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
        <NeonButton variant="primary" type="submit" :loading="creating">Create</NeonButton>
      </form>
    </BaseModal>

    <!-- Enrollment install command -->
    <BaseModal v-model="showEnroll" title="Enroll Zone" width="640px">
      <p class="hint">
        Run this one-liner on the new host (as root). It installs Docker, enrolls
        over mTLS, and starts the agent. The token is single-use and expires.
      </p>
      <pre class="install">{{ enrollCmd }}</pre>
      <NeonButton variant="ghost" @click="copyCmd"><Copy :size="13" /> Copy</NeonButton>
    </BaseModal>

    <!-- Per-zone GPU + Docker-in-Docker -->
    <BaseModal v-model="showSettings" :title="`Zone Settings · ${settingsTarget?.name ?? ''}`" width="560px">
      <form class="form" @submit.prevent="handleSettings">
        <div class="section-head">GPU acceleration</div>
        <p class="hint">
          This zone's host has its own GPU, so its render node and that node's group
          can differ from the control plane's. Leave a field empty to inherit
          Administration → Settings.
        </p>
        <label>
          GPU acceleration
          <select v-model="settingsForm.gpu_accel">
            <option value="inherit">Inherit ({{ globalSettings?.workspace_gpu_accel ? 'on' : 'off' }})</option>
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
        </label>
        <div class="row">
          <label>
            Render node
            <input v-model="settingsForm.gpu_render_node" :placeholder="globalSettings?.workspace_gpu_render_node || '/dev/dri/renderD128'" />
            <small>The DRI render node on this zone's host.</small>
          </label>
          <label>
            Render group GID
            <input v-model="settingsForm.gpu_render_gid" type="number" min="0" :placeholder="String(globalSettings?.workspace_gpu_render_gid ?? 992)" />
            <small>Fallback only: Cove detects the node's real group at launch.</small>
          </label>
        </div>

        <div class="section-head">Docker-in-Docker</div>
        <ToggleRow v-model="settingsForm.docker_enabled" :disabled="!globalSettings?.workspace_docker">
          Allow Docker-in-Docker on this zone
          <template #hint>
            <template v-if="!globalSettings?.workspace_docker">
              Turn on the Docker-in-Docker master toggle in Administration → Settings first.
            </template>
            <template v-else>
              The nested daemon runs <strong>privileged</strong>: root on this zone's host
              for whoever holds it. The agent enforces its own opt-in too, so after
              turning this on, click <strong>Enroll</strong> and re-run the installer on
              the host.
            </template>
          </template>
        </ToggleRow>
        <p v-if="settingsTarget && settingsForm.docker_enabled" class="agent-state" :class="agentDindClass">
          Agent: {{ agentDindText }}
        </p>

        <div class="form-actions">
          <NeonButton variant="ghost" type="button" @click="showSettings = false">Cancel</NeonButton>
          <NeonButton variant="primary" type="submit" :loading="savingSettings">Save</NeonButton>
        </div>
      </form>
    </BaseModal>

    <ConfirmModal
      v-model="showConfirm"
      title="Delete Zone"
      :message="`Delete zone '${deleteTarget?.name}'?`"
      confirm-label="Delete"
      :loading="deleting"
      @confirm="handleDelete"
    />

    <ConfirmModal
      v-model="showUpdate"
      title="Update Agent"
      :message="`Push the control plane's current agent image to '${updateTarget?.name}' and restart its agent? Make sure the control plane is already on the version you want to roll out. The agent will be briefly unavailable.`"
      confirm-label="Update"
      :loading="updatingId !== null"
      @confirm="handleUpdate"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { absoluteTime, relativeTime } from '@/utils/workspaceStatus'
import { computed, ref, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import NeonButton from '@/components/NeonButton.vue'
import BaseModal from '@/components/BaseModal.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import ToggleRow from '@/components/ToggleRow.vue'
import { CheckCircle2, XCircle, Plus, Trash2, KeyRound, Copy, ArrowUpCircle, SlidersHorizontal } from 'lucide-vue-next'
import { zonesApi } from '@/api/zones'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import type { AppSettings, ZoneHealth, Zone } from '@/types'

const zones = ref<Zone[]>([])
const ui = useUiStore()
const loading = ref(true)
const creating = ref(false)
// Id of the zone whose enrollment token is being minted (per-row spinner).
const enrollingId = ref<number | null>(null)
const showCreate = ref(false)
const showEnroll = ref(false)
const showConfirm = ref(false)
const enrollCmd = ref('')
const deleteTarget = ref<Zone | null>(null)
const deleting = ref(false)
const showUpdate = ref(false)
const updateTarget = ref<Zone | null>(null)
const updatingId = ref<number | null>(null)
const form = ref({ name: '', endpoint_host: '', endpoint_port: 8443 })

onMounted(async () => {
  try { await load() } finally { loading.value = false }
})
async function load() {
  zones.value = await zonesApi.list()
  loadHealth()
}
// Live daemon check per zone (the local zone has no heartbeat of its own).
const health = ref<Record<number, ZoneHealth | null>>({})
function loadHealth() {
  for (const z of zones.value) {
    zonesApi
      .health(z.id)
      .then(h => (health.value = { ...health.value, [z.id]: h }))
      .catch(() => (health.value = { ...health.value, [z.id]: { ok: false, latency_ms: null, docker_version: null, containers_running: null, error: 'check failed' } }))
  }
}
// API timestamps are naive UTC; parse them as UTC, not local time.
const formatDate = absoluteTime

async function handleCreate() {
  if (creating.value) return
  creating.value = true
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
  finally { creating.value = false }
}

async function enroll(z: Zone) {
  if (enrollingId.value !== null) return
  enrollingId.value = z.id
  try {
    const res = await zonesApi.enrollToken(z.id)
    enrollCmd.value = res.install_command
    showEnroll.value = true
    await load()
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { enrollingId.value = null }
}

function copyCmd() {
  navigator.clipboard?.writeText(enrollCmd.value)
  ui.toast('Copied', 'success')
}

function confirmUpdate(z: Zone) { updateTarget.value = z; showUpdate.value = true }
async function handleUpdate() {
  if (!updateTarget.value) return
  const z = updateTarget.value
  updatingId.value = z.id
  showUpdate.value = false
  try {
    const res = await zonesApi.updateAgent(z.id)
    ui.toast(res.detail || `Updating ${z.name}…`, 'info')
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    updatingId.value = null
  }
}

// --- Per-zone settings ------------------------------------------------------
const showSettings = ref(false)
const settingsTarget = ref<Zone | null>(null)
const savingSettings = ref(false)
const globalSettings = ref<AppSettings | null>(null)
const settingsForm = ref({
  gpu_accel: 'inherit' as 'inherit' | 'on' | 'off',
  gpu_render_node: '',
  gpu_render_gid: '' as string | number,
  docker_enabled: false,
})

async function openSettings(z: Zone) {
  settingsTarget.value = z
  settingsForm.value = {
    gpu_accel: z.gpu_accel === null ? 'inherit' : z.gpu_accel ? 'on' : 'off',
    gpu_render_node: z.gpu_render_node ?? '',
    gpu_render_gid: z.gpu_render_gid ?? '',
    docker_enabled: z.docker_enabled,
  }
  showSettings.value = true
  try {
    globalSettings.value = await adminApi.settings.get()
  } catch {
    globalSettings.value = null
  }
}

// The agent's own DinD opt-in: the zone list reports what was last seen, the
// live health check refreshes it.
const agentDind = computed(() => {
  const z = settingsTarget.value
  if (!z) return null
  const live = health.value[z.id]?.agent_dind
  return live ?? z.agent_allows_docker
})
const agentDindText = computed(() =>
  agentDind.value === true
    ? 'allows Docker-in-Docker.'
    : agentDind.value === false
      ? 'does not allow Docker-in-Docker yet. Click Enroll and re-run the installer on the host.'
      : 'unknown (the agent is unreachable or predates this setting). Update or re-enroll it.'
)
const agentDindClass = computed(() => (agentDind.value === true ? 'ok' : 'bad'))

async function handleSettings() {
  const z = settingsTarget.value
  if (!z || savingSettings.value) return
  savingSettings.value = true
  const f = settingsForm.value
  const gid = String(f.gpu_render_gid).trim()
  try {
    await zonesApi.update(z.id, {
      gpu_accel: f.gpu_accel === 'inherit' ? null : f.gpu_accel === 'on',
      gpu_render_node: f.gpu_render_node.trim() || null,
      gpu_render_gid: gid === '' ? null : Number(gid),
      docker_enabled: f.docker_enabled,
    })
    showSettings.value = false
    await load()
    ui.toast('Zone settings saved', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { savingSettings.value = false }
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
  font-family: var(--font-mono); font-size: 12px; line-height: 1.5;
  color: var(--text-muted); margin: 0 0 16px; max-width: 70ch;
}
.health { display: inline-flex; align-items: center; gap: 5px; font-family: var(--font-mono); font-size: 12px; white-space: nowrap; }
.health.ok { color: var(--green); }
.health.bad { color: var(--red); }
.muted { color: var(--text-muted); }
.badge {
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 1px 6px; color: var(--text-muted); text-transform: uppercase;
}
.badge.enrolled { color: var(--accent); border-color: var(--accent); }
.badge.offline, .badge.error { color: #ff6b6b; border-color: #ff6b6b; }
.form { display: flex; flex-direction: column; gap: 12px; }
.form label { display: flex; flex-direction: column; gap: 4px; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }
.form input, .form select { padding: 6px 8px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text); font-family: var(--font-mono); }
.section-head {
  font-size: 12px; font-weight: 600; letter-spacing: 0.5px; color: var(--accent);
  padding-bottom: 6px; border-bottom: 1px solid var(--border);
}
.form .hint { margin: 0; }
.form-actions { display: flex; gap: 8px; justify-content: flex-end; }
.agent-state { font-family: var(--font-mono); font-size: 11px; margin: 0; }
.agent-state.ok { color: var(--green); }
.agent-state.bad { color: var(--amber); }
.form small { color: var(--text-muted); font-size: 11px; }
.row { display: flex; gap: 12px; }
.row label { flex: 1; }
.install {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 10px; font-family: var(--font-mono); font-size: 12px; color: var(--text);
  white-space: pre-wrap; word-break: break-all; margin: 0 0 12px;
}
</style>
