<template>
  <div class="task">
    <div class="line">
      <component :is="icon" :size="13" class="icon" :class="[task.state, { spin: task.state === 'running' }]" />
      <span class="what">{{ task.op }} <b>{{ task.apps.join(', ') }}</b></span>
    </div>
    <div class="meta">
      <span class="state" :class="task.state">{{ stateLabel }}</span>
      <span v-if="progress">{{ progress }}</span>
      <span v-if="task.failed_apps.length" class="failed">failed: {{ task.failed_apps.join(', ') }}</span>
      <span v-if="when">{{ when }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CheckCircle2, Clock, Loader2, OctagonX, TriangleAlert } from 'lucide-vue-next'
import { OP_ACTIVE } from '@/stores/tasks'
import type { ProotTask } from '@/types'

const props = defineProps<{ task: ProotTask }>()

const icon = computed(() => ({
  queued: Clock,
  running: Loader2,
  done: CheckCircle2,
  failed: OctagonX,
  interrupted: TriangleAlert,
}[props.task.state]))

const stateLabel = computed(() => (props.task.state === 'running' ? OP_ACTIVE[props.task.op] : props.task.state))

const progress = computed(() => {
  const t = props.task
  if (t.state !== 'running' || t.apps.length < 2) return ''
  return `${Math.min(t.done_count + 1, t.apps.length)}/${t.apps.length}${t.current_app ? ` · ${t.current_app}` : ''}`
})

function ago(epoch: number) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - epoch))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const when = computed(() => {
  const t = props.task
  const at = t.finished_at ?? t.started_at ?? t.created_at
  return at ? ago(at) : ''
})
</script>

<style scoped>
.task { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.line { display: flex; align-items: center; gap: 7px; min-width: 0; }
.what { font-size: 12px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.what b { color: var(--text); font-weight: 600; font-family: var(--font-mono); }
.meta { display: flex; flex-wrap: wrap; gap: 8px; padding-left: 20px; font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); letter-spacing: 0.5px; }
.icon { flex-shrink: 0; }
.icon.queued, .state.queued { color: var(--text-muted); }
.icon.running, .state.running { color: var(--amber); }
.icon.done, .state.done { color: var(--green); }
.icon.failed, .state.failed, .failed { color: var(--red); }
.icon.interrupted, .state.interrupted { color: var(--amber); }
.state { text-transform: uppercase; letter-spacing: 1px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
