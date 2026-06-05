<template>
  <AppShell>
    <div class="page-header">
      <h2>// AUDIT LOG</h2>
      <NeonButton variant="secondary" @click="load">Refresh</NeonButton>
    </div>
    <div v-if="!entries.length" class="empty">No audit entries.</div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>User</th>
            <th>Action</th>
            <th>Detail</th>
            <th>IP</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in entries" :key="entry.id">
            <td>{{ formatDate(entry.ts) }}</td>
            <td>{{ entry.username || '—' }}</td>
            <td><code>{{ entry.action }}</code></td>
            <td class="detail" :title="entry.detail || ''">{{ entry.detail || '—' }}</td>
            <td>{{ entry.ip || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import type { AuditEntry } from '@/types'

const entries = ref<AuditEntry[]>([])
const ui = useUiStore()

async function load() {
  try {
    entries.value = await adminApi.audit.list()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  }
}
onMounted(load)

function formatDate(d: string) { return new Date(d).toLocaleString() }
</script>

<style scoped>
@import '@/styles/tables.css';
.detail {
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
