<template>
  <BaseModal v-model="open" :title="`${name} · Resources`" width="420px">
    <div class="metrics">
      <template v-if="stat">
        <div class="metric">
          <div class="metric-head">
            <span class="metric-label"><Cpu :size="13" /> CPU</span>
            <span class="metric-val">{{ stat.cpu_pct.toFixed(0) }}%</span>
          </div>
          <div class="meter"><span class="fill" :class="band(stat.cpu_pct)" :style="{ width: barWidth(stat.cpu_pct) }" /></div>
        </div>

        <div class="metric">
          <div class="metric-head">
            <span class="metric-label"><MemoryStick :size="13" /> Memory</span>
            <span class="metric-val">
              {{ fmtBytes(stat.mem_used) }}<template v-if="stat.mem_limit"> / {{ fmtBytes(stat.mem_limit) }}</template>
              <span class="muted" v-if="stat.mem_limit"> ({{ stat.mem_pct.toFixed(0) }}%)</span>
              <span class="muted" v-else> · no limit</span>
            </span>
          </div>
          <div class="meter"><span class="fill" :class="band(stat.mem_pct)" :style="{ width: barWidth(stat.mem_pct) }" /></div>
        </div>

        <p class="hint">Live container usage, refreshed every few seconds. CPU can exceed 100% across cores.</p>
      </template>
      <p v-else class="hint collecting">Collecting usage…</p>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, watch, onUnmounted } from 'vue'
import { Cpu, MemoryStick } from 'lucide-vue-next'
import BaseModal from './BaseModal.vue'
import { useWorkspacesStore } from '@/stores/workspaces'

const props = defineProps<{ wsId: number; name: string }>()
const open = defineModel<boolean>({ default: false })

const store = useWorkspacesStore()
const stat = computed(() => store.stats[props.wsId])

// Poll a bit faster than the background 5s cadence while the modal is open, so
// the numbers feel live; stop when it closes.
let timer: ReturnType<typeof setInterval> | null = null
function stop() {
  if (timer) { clearInterval(timer); timer = null }
}
watch(open, (v) => {
  if (v) {
    store.fetchStats()
    stop()
    timer = setInterval(() => store.fetchStats(), 3000)
  } else {
    stop()
  }
})
onUnmounted(stop)

function barWidth(pct: number): string {
  return `${Math.max(0, Math.min(100, pct))}%`
}
function band(pct: number): string {
  return pct >= 90 ? 'red' : pct >= 70 ? 'amber' : 'green'
}
function fmtBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`
  if (n >= 1024) return `${Math.round(n / 1024)} KB`
  return `${n} B`
}
const name = computed(() => props.name)
</script>

<style scoped>
.metrics { display: flex; flex-direction: column; gap: 18px; }
.metric { display: flex; flex-direction: column; gap: 6px; }
.metric-head { display: flex; align-items: baseline; justify-content: space-between; }
.metric-label {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted);
}
.metric-val { font-family: var(--font-mono); font-size: 13px; color: var(--text); }
.metric-val .muted { color: var(--text-muted); }
.meter { height: 8px; border-radius: 4px; background: rgba(255,255,255,0.06); overflow: hidden; }
.fill { display: block; height: 100%; border-radius: 4px; transition: width 0.4s ease; }
.fill.green { background: var(--green, #00ff9d); }
.fill.amber { background: #ffb020; }
.fill.red { background: var(--red, #ff3b6b); }
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 0; }
.collecting { padding: 12px 0; text-align: center; }
</style>
