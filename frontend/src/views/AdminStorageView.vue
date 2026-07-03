<template>
  <AppShell>
    <div class="page-header">
      <h2>// STORAGE</h2>
      <NeonButton variant="ghost" :loading="loading" @click="load">
        <RefreshCw :size="14" /> Refresh
      </NeonButton>
    </div>
    <p class="hint">
      Disk usage per node. Workspaces, images, and build cache all consume the host
      disk — when it fills, containers fail to start and streams drop. Prune
      reclaims Docker space; it never removes named volumes (workspace data).
    </p>

    <div v-if="loading && !info" class="empty">LOADING…</div>

    <div v-else class="zones">
      <section v-for="z in info?.zones ?? []" :key="z.zone_id" class="zone-card">
        <header class="zone-head">
          <h3>{{ z.zone_name }}<span v-if="z.zone_id === 0" class="tag">control plane</span></h3>
          <div class="zone-actions">
            <NeonButton
              variant="ghost"
              :loading="pruningId === z.zone_id && !deepPruning"
              :disabled="!z.online || pruningId !== null"
              @click="confirmPrune(z, false)"
            ><Trash2 :size="13" /> Prune</NeonButton>
            <NeonButton
              variant="danger"
              :loading="pruningId === z.zone_id && deepPruning"
              :disabled="!z.online || pruningId !== null"
              @click="confirmPrune(z, true)"
            ><Flame :size="13" /> Deep Prune</NeonButton>
          </div>
        </header>

        <div v-if="!z.online" class="offline">
          Unreachable — {{ z.error || 'daemon did not respond' }}
        </div>

        <template v-else>
          <!-- Host disk bar -->
          <div v-if="z.host" class="disk">
            <div class="disk-labels">
              <span>Host disk</span>
              <span :class="{ warn: usedPct(z.host) >= 80, crit: usedPct(z.host) >= 90 }">
                {{ fmt(z.host.used) }} / {{ fmt(z.host.total) }} used
                · {{ fmt(z.host.free) }} free
              </span>
            </div>
            <div class="bar">
              <div
                class="bar-fill"
                :class="{ warn: usedPct(z.host) >= 80, crit: usedPct(z.host) >= 90 }"
                :style="{ width: usedPct(z.host) + '%' }"
              />
            </div>
          </div>
          <p v-else class="disk-none">Host free-space unavailable for this node.</p>

          <!-- Docker breakdown -->
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>Type</th><th>Items</th><th>Size</th><th>Reclaimable</th></tr>
              </thead>
              <tbody>
                <tr v-for="c in z.categories" :key="c.key">
                  <td>{{ c.label }}</td>
                  <td>{{ c.active }} / {{ c.total }}</td>
                  <td>{{ fmt(c.size) }}</td>
                  <td :class="{ reclaim: c.reclaimable > 0 }">
                    {{ fmt(c.reclaimable) }}
                    <span v-if="c.key === 'volumes' && c.reclaimable > 0" class="note">
                      (not pruned)
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </section>
    </div>

    <ConfirmModal
      v-model="showConfirm"
      :title="deepPruning ? 'Deep Prune' : 'Prune Docker Storage'"
      :message="confirmMessage"
      :confirm-label="deepPruning ? 'Deep Prune' : 'Prune'"
      :loading="pruningId !== null"
      @confirm="doPrune"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { RefreshCw, Trash2, Flame } from 'lucide-vue-next'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import type { StorageInfo, ZoneStorage, HostDisk } from '@/types'

const ui = useUiStore()
const info = ref<StorageInfo | null>(null)
const loading = ref(false)
const showConfirm = ref(false)
const deepPruning = ref(false)
const pruningId = ref<number | null>(null)
const target = ref<ZoneStorage | null>(null)

function fmt(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const val = bytes / Math.pow(1024, i)
  return `${val >= 100 || i === 0 ? Math.round(val) : val.toFixed(1)} ${units[i]}`
}

function usedPct(h: HostDisk): number {
  if (!h.total) return 0
  return Math.min(100, Math.round((h.used / h.total) * 100))
}

async function load() {
  loading.value = true
  try {
    info.value = await adminApi.storage.get()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

const confirmMessage = computed(() => {
  const name = target.value?.zone_name ?? ''
  return deepPruning.value
    ? `Deep-prune '${name}'? This removes ALL unused images and every stopped ` +
        `container, not just dangling ones. Images will have to be re-pulled or ` +
        `rebuilt on next use (slow). Named volumes are never touched.`
    : `Prune '${name}'? This removes dangling (untagged) images and the build ` +
        `cache. Running workspaces and their data are unaffected.`
})

function confirmPrune(z: ZoneStorage, deep: boolean) {
  target.value = z
  deepPruning.value = deep
  showConfirm.value = true
}

async function doPrune() {
  if (!target.value) return
  const z = target.value
  pruningId.value = z.zone_id
  showConfirm.value = false
  try {
    const res = await adminApi.storage.prune(z.zone_id, deepPruning.value)
    ui.toast(`Reclaimed ${fmt(res.space_reclaimed)} on ${z.zone_name}`, 'success')
    await load()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    pruningId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
@import '@/styles/tables.css';
.hint {
  font-family: var(--font-mono); font-size: 11px; line-height: 1.5;
  color: var(--text-muted); margin: 0 0 16px; max-width: 74ch;
}
.zones { display: flex; flex-direction: column; gap: 20px; }
.zone-card {
  border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px; background: var(--surface, transparent);
}
.zone-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 14px; flex-wrap: wrap;
}
.zone-head h3 {
  margin: 0; font-size: 14px; font-family: var(--font-mono);
  display: flex; align-items: center; gap: 8px;
}
.tag {
  font-size: 9px; letter-spacing: 1px; text-transform: uppercase;
  color: var(--text-muted); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 1px 6px;
}
.zone-actions { display: flex; gap: 8px; }
.offline {
  font-family: var(--font-mono); font-size: 11px; color: #ff6b6b;
  padding: 8px 0;
}
.disk { margin-bottom: 16px; }
.disk-labels {
  display: flex; justify-content: space-between; gap: 12px;
  font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);
  margin-bottom: 6px;
}
.disk-labels .warn { color: #f5a623; }
.disk-labels .crit { color: #ff6b6b; }
.disk-none { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin: 0 0 14px; }
.bar {
  height: 8px; border-radius: 4px; overflow: hidden;
  background: var(--border);
}
.bar-fill {
  height: 100%; background: var(--accent); border-radius: 4px;
  transition: width 0.3s ease;
}
.bar-fill.warn { background: #f5a623; }
.bar-fill.crit { background: #ff6b6b; }
td.reclaim { color: var(--accent); }
.note { color: var(--text-muted); font-size: 10px; }
</style>
