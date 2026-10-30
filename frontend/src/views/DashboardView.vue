<template>
  <AppShell wide>
    <div class="page-header">
      <div>
        <h2>// WORKSPACES</h2>
        <p class="subtitle">
          <template v-if="limits?.max_workspaces">{{ store.items.length }} of {{ limits.max_workspaces }} workspaces</template>
          <template v-else>{{ store.items.length }} workspace{{ store.items.length !== 1 ? 's' : '' }}</template>
          <template v-if="limits?.max_running"> · {{ slotsUsed }} of {{ limits.max_running }} running</template>
          <template v-else-if="store.items.length"> · {{ running.length }} running</template>
        </p>
        <p v-if="atTotalCap" class="subtitle cap-note">
          That's as many workspaces as your account may have. Delete one to launch another.
        </p>
      </div>
      <div class="header-actions">
        <NeonButton
          variant="primary"
          :disabled="atTotalCap"
          :title="atTotalCap ? `Your account may have at most ${limits?.max_workspaces} workspaces` : undefined"
          @click="showLaunch = true"
        ><Plus :size="15" /> LAUNCH</NeonButton>
      </div>
    </div>

    <!-- Totals across running workspaces: is anything eating the host? -->
    <dl v-if="running.length" class="summary" aria-label="Running totals">
      <div><dt><Activity :size="12" /> Running</dt><dd>{{ running.length }}</dd></div>
      <div><dt><Cpu :size="12" /> CPU</dt><dd>{{ totals.cpu.toFixed(0) }}%</dd></div>
      <div><dt><MemoryStick :size="12" /> Memory</dt><dd>{{ fmtBytes(totals.mem) }}</dd></div>
      <div v-if="starting.length"><dt><Loader :size="12" /> Starting</dt><dd>{{ starting.length }}</dd></div>
      <div v-if="attention.length" class="bad"><dt><AlertTriangle :size="12" /> Errors</dt><dd>{{ attention.length }}</dd></div>
    </dl>

    <div v-if="store.loading && !store.items.length" class="empty-state">
      <LoadingSpinner />
      <span class="mono">loading workspaces…</span>
    </div>

    <div v-else-if="!store.items.length" class="empty-state">
      <MonitorOff class="empty-icon" :size="72" :stroke-width="1.25" />
      <p class="empty-title">No workspaces yet</p>
      <p class="empty-sub">
        A workspace is a full Linux desktop, a single app, or a browser that runs in a
        container and streams into this tab.
      </p>
      <NeonButton variant="primary" @click="showLaunch = true"><Plus :size="15" /> LAUNCH YOUR FIRST WORKSPACE</NeonButton>
    </div>

    <template v-else>
      <!-- Crashed / failed workspaces get their own section at the top instead
           of sinking into the stopped grid. -->
      <section v-if="attention.length" class="section attention">
        <h3 class="section-title"><AlertTriangle :size="13" /> NEEDS ATTENTION <span class="count">{{ attention.length }}</span></h3>
        <div class="grid">
          <WorkspaceCard v-for="ws in attention" :key="ws.id" :ws="ws" />
        </div>
      </section>

      <section v-if="active.length" class="section">
        <h3 class="section-title"><Activity :size="13" /> ACTIVE <span class="count">{{ active.length }}</span></h3>
        <div class="grid">
          <WorkspaceCard
            v-for="ws in active"
            :key="ws.id"
            :ws="ws"
            :stats="store.stats[ws.id] ?? null"
          />
        </div>
      </section>

      <section v-if="offline.length" class="section">
        <h3 class="section-title"><PowerOff :size="13" /> STOPPED <span class="count">{{ offline.length }}</span></h3>
        <div class="grid offline-grid">
          <WorkspaceCard v-for="ws in offline" :key="ws.id" :ws="ws" />
        </div>
      </section>
    </template>

    <LaunchWizard v-model="showLaunch" />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import AppShell from '@/components/AppShell.vue'
import WorkspaceCard from '@/components/WorkspaceCard.vue'
import LaunchWizard from '@/components/LaunchWizard.vue'
import NeonButton from '@/components/NeonButton.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import { Plus, MonitorOff, Activity, PowerOff, AlertTriangle, Cpu, MemoryStick, Loader } from 'lucide-vue-next'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useZonesStore } from '@/stores/zones'
import { useThinClientStore } from '@/stores/thinClient'
import { useRouter } from 'vue-router'
import { fmtBytes, parseTs } from '@/utils/workspaceStatus'
import { workspacesApi } from '@/api/workspaces'
import type { WorkspaceLimits } from '@/types'

const store = useWorkspacesStore()
const zonesStore = useZonesStore()
const thinClient = useThinClientStore()
const router = useRouter()
const showLaunch = ref(false)

// Live/transitioning workspaces float to the top (with stats); errors get their
// own section above them; idle ones sit below.
const ACTIVE = new Set(['running', 'creating', 'stopping', 'migrating'])
const active = computed(() => store.items.filter(ws => ACTIVE.has(ws.status)))
const attention = computed(() =>
  store.items
    .filter(ws => ws.status === 'error')
    .sort((a, b) => (parseTs(b.stopped_at ?? b.started_at) ?? 0) - (parseTs(a.stopped_at ?? a.started_at) ?? 0)),
)
// Most recently used first: that's the one you're most likely to want back.
const offline = computed(() =>
  store.items
    .filter(ws => ws.status === 'stopped')
    .sort((a, b) => (parseTs(b.stopped_at ?? b.created_at) ?? 0) - (parseTs(a.stopped_at ?? a.created_at) ?? 0)),
)
const running = computed(() => store.items.filter(ws => ws.status === 'running'))
const starting = computed(() =>
  store.items.filter(ws => ws.status === 'creating' || (ws.status === 'running' && !ws.connectable)),
)
// The account's caps (0 = none). Usage is counted from the live list, so the
// numbers move with every poll; the caps themselves only change when an admin
// edits them, so they're re-read when the number of workspaces changes.
const limits = ref<WorkspaceLimits | null>(null)
const slotsUsed = computed(() => store.items.filter(ws => ws.status === 'creating' || ws.status === 'running').length)
const atTotalCap = computed(() => !!limits.value?.max_workspaces && store.items.length >= limits.value.max_workspaces)
function loadLimits() {
  workspacesApi.limits().then(l => { limits.value = l }).catch(() => {})
}
watch(() => store.items.length, loadLimits)

const totals = computed(() => {
  let cpu = 0
  let mem = 0
  for (const s of Object.values(store.stats)) {
    cpu += s.cpu_pct
    mem += s.mem_used
  }
  return { cpu, mem }
})

onMounted(() => {
  store.startPolling()
  // On a thin client the device policy may open a workspace straight away.
  store
    .fetch()
    .then(() => thinClient.maybeAutoOpen(store.items, router, id => store.start(id)))
    .catch(() => {})
  zonesStore.fetch()
  loadLimits()
})
onUnmounted(() => store.stopPolling())
</script>

<style scoped>
.page-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 28px; gap: 16px;
}
h2 {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 3px;
  color: var(--accent);
  text-shadow: var(--glow-sm);
}
.subtitle { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); margin-top: 3px; }
.cap-note { color: var(--amber); }
.header-actions { display: flex; gap: 8px; }

.section { margin-bottom: 32px; }
.section:last-child { margin-bottom: 0; }
.section-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--text-muted);
  text-transform: uppercase;
  margin-bottom: 14px;
}
.section-title svg { color: var(--accent); }
.section-title .count {
  font-size: 11px;
  color: var(--accent);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0 6px;
  line-height: 16px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  /* Equalize every row to the tallest card so the whole grid is uniform,
     not just cards within a single row. */
  grid-auto-rows: 1fr;
  align-items: stretch;
  gap: 16px;
}
/* Idle nodes are denser — they carry less info than active ones. */
.offline-grid { grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }

@media (max-width: 640px) {
  .page-header {
    flex-direction: column;
    align-items: stretch;
    margin-bottom: 20px;
  }
  .header-actions { width: 100%; }
  .header-actions > * { flex: 1; }
  .grid { grid-template-columns: 1fr; }
}
.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 16px; padding: 12vh 24px; color: var(--text-muted); text-align: center;
  border: 1px dashed var(--border); border-radius: var(--radius);
}
.empty-title { font-family: var(--font-display); font-size: 16px; letter-spacing: 2px; color: var(--text); }
.empty-sub { max-width: 46ch; font-size: 14px; line-height: 1.6; }

.summary {
  display: flex; flex-wrap: wrap; gap: 8px; margin: -12px 0 28px;
}
.summary > div {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 8px 14px; min-width: 110px;
}
.summary dt {
  display: flex; align-items: center; gap: 6px;
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 1.5px; text-transform: uppercase;
  color: var(--text-muted);
}
.summary dd { font-family: var(--font-mono); font-size: 18px; color: var(--accent); }
.summary .bad dd, .summary .bad dt { color: var(--red); }
.attention .section-title, .attention .section-title svg { color: var(--red); }
.empty-icon {
  color: var(--accent);
  opacity: 0.5;
  filter: drop-shadow(var(--glow-sm));
}
.mono { font-family: var(--font-mono); font-size: 13px; letter-spacing: 2px; }
</style>
