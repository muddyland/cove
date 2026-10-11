<template>
  <AppShell>
    <div class="page-header">
      <div>
        <h2>// RUNNING WORKSPACES</h2>
        <p class="subtitle">Every user's running and starting workspaces. Refreshes every 10 s.</p>
      </div>
      <NeonButton variant="secondary" :loading="loading" @click="load"><RefreshCw :size="14" /> Refresh</NeonButton>
    </div>
    <LoadingSpinner v-if="loading && !loaded" block />
    <div v-else-if="!sessions.length" class="empty">Nothing is running right now.</div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Workspace</th>
            <th>Owner</th>
            <th>Status</th>
            <th>Image</th>
            <th v-if="hasZones">Zone</th>
            <th>Uptime</th>
            <th><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ws in sessions" :key="ws.id">
            <td>{{ ws.name }}</td>
            <td>{{ owners[ws.user_id] ?? `#${ws.user_id}` }}</td>
            <td><StatusBadge :status="displayStatus(ws)" /></td>
            <td>{{ ws.image_name }}</td>
            <td v-if="hasZones">{{ ws.zone_name || 'Local' }}</td>
            <td :title="ws.started_at ? `Started ${formatDate(ws.started_at)}` : ''">
              {{ ws.started_at && ws.status === 'running' ? fmtDuration(now - (parseTs(ws.started_at) ?? now)) : '—' }}
            </td>
            <td class="actions">
              <NeonButton variant="ghost" @click="confirmKill(ws)"><Square :size="13" /> Stop</NeonButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <ConfirmModal
      v-model="showConfirm"
      title="Stop workspace"
      :message="`Stop '${killTarget?.name}' (owned by ${killTarget ? owners[killTarget.user_id] ?? 'another user' : ''})? It stops immediately and its user is disconnected.`"
      confirm-label="Stop"
      :loading="killing"
      @confirm="handleKill"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { absoluteTime, displayStatus, fmtDuration, parseTs } from '@/utils/workspaceStatus'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import { RefreshCw, Square } from 'lucide-vue-next'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import { useNow } from '@/composables/useNow'
import type { Workspace } from '@/types'

const sessions = ref<Workspace[]>([])
const owners = ref<Record<number, string>>({})
const ui = useUiStore()
const showConfirm = ref(false)
const killTarget = ref<Workspace | null>(null)
const killing = ref(false)
const loading = ref(false)
const loaded = ref(false)
const now = useNow(15_000)
const hasZones = computed(() => sessions.value.some(ws => ws.zone_id !== 0))

// API timestamps are naive UTC; parse them as UTC, not local time.
const formatDate = absoluteTime

async function load() {
  loading.value = true
  try {
    sessions.value = await adminApi.sessions.list()
    loaded.value = true
  } catch (e: any) {
    ui.toast(e.message || 'Could not load running workspaces', 'error')
  } finally {
    loading.value = false
  }
}
async function loadOwners() {
  try {
    const users = await adminApi.users.list()
    owners.value = Object.fromEntries(users.map(u => [u.id, u.username]))
  } catch {
    // Owner names are a nicety; the id fallback still identifies the row.
  }
}

let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  load()
  loadOwners()
  timer = setInterval(() => { if (!document.hidden) load() }, 10_000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })

function confirmKill(ws: Workspace) { killTarget.value = ws; showConfirm.value = true }

async function handleKill() {
  if (!killTarget.value) return
  killing.value = true
  try {
    await adminApi.sessions.kill(killTarget.value.id)
    await load()
    showConfirm.value = false
    ui.toast('Workspace stopped', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { killing.value = false }
}
</script>

<style scoped>
@import '@/styles/tables.css';
.subtitle { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); margin-top: 4px; }
</style>
