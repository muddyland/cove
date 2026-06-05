<template>
  <span class="badge" :class="status"><component :is="icon" class="badge-icon" :size="11" />{{ label }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CircleDot, Circle, Loader, CircleAlert } from 'lucide-vue-next'
import type { WorkspaceStatus } from '@/types'

const props = defineProps<{ status: WorkspaceStatus }>()

const label = computed(() => ({
  creating: 'Starting',
  running: 'Online',
  stopping: 'Halting',
  stopped: 'Offline',
  error: 'Error',
}[props.status] ?? props.status))

const icon = computed(() => ({
  creating: Loader,
  running: CircleDot,
  stopping: Loader,
  stopped: Circle,
  error: CircleAlert,
}[props.status] ?? Circle))
</script>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  font-family: var(--font-mono);
  border: 1px solid currentColor;
  border-radius: var(--radius-sm);
}
.badge::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.badge-icon { flex-shrink: 0; }
.creating .badge-icon, .stopping .badge-icon { animation: spin 1.2s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.running  { color: var(--green); box-shadow: 0 0 6px rgba(0,255,157,0.3); }
.running::before { box-shadow: 0 0 4px var(--green); animation: pulse 1.5s infinite; }
.creating, .stopping { color: var(--amber); animation: flicker 1s infinite; }
.stopped  { color: var(--text-muted); border-color: var(--border); }
.error    { color: var(--red); box-shadow: 0 0 6px rgba(255,32,85,0.3); }

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
@keyframes flicker { 0%, 100% { opacity: 1; } 45% { opacity: 0.7; } 50% { opacity: 1; } 55% { opacity: 0.6; } }
</style>
