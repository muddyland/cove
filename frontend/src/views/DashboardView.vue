<template>
  <AppShell>
    <div class="page-header">
      <div>
        <h2>// WORKSPACE GRID</h2>
        <p class="subtitle">{{ store.items.length }} node{{ store.items.length !== 1 ? 's' : '' }} allocated</p>
      </div>
      <div class="header-actions">
        <NeonButton variant="secondary" @click="showWebsite = true"><Globe :size="15" /> OPEN WEBSITE</NeonButton>
        <NeonButton variant="primary" @click="showLaunch = true"><Plus :size="15" /> DEPLOY NODE</NeonButton>
      </div>
    </div>

    <div v-if="store.loading && !store.items.length" class="empty-state">
      <span class="mono">scanning...</span>
    </div>

    <div v-else-if="!store.items.length" class="empty-state">
      <MonitorOff class="empty-icon" :size="64" :stroke-width="1.25" />
      <p class="mono">no nodes allocated</p>
      <NeonButton variant="primary" @click="showLaunch = true"><Plus :size="15" /> DEPLOY FIRST NODE</NeonButton>
    </div>

    <div v-else class="grid">
      <WorkspaceCard v-for="ws in store.items" :key="ws.id" :ws="ws" />
    </div>

    <LaunchModal v-model="showLaunch" />
    <OpenWebsiteModal v-model="showWebsite" />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import WorkspaceCard from '@/components/WorkspaceCard.vue'
import LaunchModal from '@/components/LaunchModal.vue'
import OpenWebsiteModal from '@/components/OpenWebsiteModal.vue'
import NeonButton from '@/components/NeonButton.vue'
import { Globe, Plus, MonitorOff } from 'lucide-vue-next'
import { useWorkspacesStore } from '@/stores/workspaces'

const store = useWorkspacesStore()
const showLaunch = ref(false)
const showWebsite = ref(false)

onMounted(() => store.fetch())
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
.subtitle { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin-top: 3px; }
.header-actions { display: flex; gap: 8px; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  /* Equalize every row to the tallest card so the whole grid is uniform,
     not just cards within a single row. */
  grid-auto-rows: 1fr;
  align-items: stretch;
  gap: 16px;
}

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
  gap: 16px; padding: 80px 24px; color: var(--text-muted); text-align: center;
}
.empty-icon {
  color: var(--accent);
  opacity: 0.5;
  filter: drop-shadow(var(--glow-sm));
}
.mono { font-family: var(--font-mono); font-size: 12px; letter-spacing: 2px; }
</style>
