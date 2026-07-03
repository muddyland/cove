<template>
  <AppShell>
    <div class="page-header">
      <h2>// ACTIVE SESSIONS</h2>
      <NeonButton variant="secondary" @click="load"><RefreshCw :size="14" /> Refresh</NeonButton>
    </div>
    <div v-if="!sessions.length" class="empty">No active sessions.</div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Workspace</th>
            <th>Status</th>
            <th>Image</th>
            <th>Started</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ws in sessions" :key="ws.id">
            <td>{{ ws.name }}</td>
            <td><StatusBadge :status="ws.status" /></td>
            <td>{{ ws.image_name }}</td>
            <td>{{ ws.started_at ? formatDate(ws.started_at) : '—' }}</td>
            <td class="actions">
              <NeonButton variant="danger" @click="confirmKill(ws)"><Square :size="13" /> Halt</NeonButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <ConfirmModal
      v-model="showConfirm"
      title="Halt Session"
      :message="`Halt '${killTarget?.name}'? It will stop immediately.`"
      confirm-label="Halt"
      :loading="killing"
      @confirm="handleKill"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { RefreshCw, Square } from 'lucide-vue-next'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import type { Workspace } from '@/types'

const sessions = ref<Workspace[]>([])
const ui = useUiStore()
const showConfirm = ref(false)
const killTarget = ref<Workspace | null>(null)
const killing = ref(false)

async function load() { sessions.value = await adminApi.sessions.list() }
onMounted(load)

function formatDate(d: string) { return new Date(d).toLocaleString() }
function confirmKill(ws: Workspace) { killTarget.value = ws; showConfirm.value = true }

async function handleKill() {
  if (!killTarget.value) return
  killing.value = true
  try {
    await adminApi.sessions.kill(killTarget.value.id)
    await load()
    showConfirm.value = false
    ui.toast('Session halted', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { killing.value = false }
}
</script>

<style scoped>
@import '@/styles/tables.css';
</style>
