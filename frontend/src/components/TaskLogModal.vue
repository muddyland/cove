<template>
  <BaseModal v-model="open" :title="title" width="720px">
    <div class="task-log">
      <div class="output-head">
        <span class="output-label">{{ subtitle }}</span>
        <NeonButton variant="ghost" :loading="loading" @click="load">
          <RefreshCw :size="13" /> Refresh
        </NeonButton>
      </div>
      <div v-if="loading && !text && !error" class="output"><LoadingSpinner block /></div>
      <pre v-else ref="pre" class="output" :class="{ muted: !text }">{{ body }}</pre>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import LoadingSpinner from './LoadingSpinner.vue'
import { RefreshCw } from 'lucide-vue-next'
import { prootApi } from '@/api/proot'
import { isActiveTask, useTasksStore } from '@/stores/tasks'
import type { ProotTask } from '@/types'

const props = defineProps<{ wsId: number; task: ProotTask | null; workspaceName?: string }>()
const open = defineModel<boolean>({ default: false })

const tasks = useTasksStore()
const text = ref('')
const error = ref('')
const loading = ref(false)
const pre = ref<HTMLElement | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
// Keep task states fresh while open, even on pages without the navbar.
let unsubscribe: (() => void) | null = null

// The freshest copy of this task (the prop is a snapshot from when it opened).
const live = computed(() => {
  if (!props.task) return null
  const group = tasks.groups.find(g => g.workspace_id === props.wsId)
  return group?.tasks.find(t => t.id === props.task!.id) ?? props.task
})

const title = computed(() => (live.value ? `${live.value.op} · ${live.value.apps.join(', ')}` : 'Task log'))
const subtitle = computed(() => {
  const t = live.value
  if (!t) return ''
  const where = props.workspaceName ? `${props.workspaceName} · ` : ''
  return `${where}${t.state}${t.current_app ? ` · ${t.current_app}` : ''}`
})
const body = computed(() => {
  if (error.value) return error.value
  return text.value || '(no output yet)'
})

async function load() {
  if (!props.task) return
  loading.value = true
  try {
    const atBottom = !pre.value || pre.value.scrollHeight - pre.value.scrollTop - pre.value.clientHeight < 24
    text.value = (await prootApi.taskLog(props.wsId, props.task.id)).output
    error.value = ''
    // Follow the tail unless the user scrolled up to read.
    if (atBottom) {
      await nextTick()
      if (pre.value) pre.value.scrollTop = pre.value.scrollHeight
    }
  } catch (e: any) {
    error.value = e?.message || 'Failed to load the log.'
  } finally {
    loading.value = false
  }
}

function stopTimer() {
  if (timer) clearInterval(timer)
  timer = null
  unsubscribe?.()
  unsubscribe = null
}

watch(open, value => {
  stopTimer()
  if (!value) return
  unsubscribe = tasks.subscribe()
  text.value = ''
  error.value = ''
  load()
  timer = setInterval(() => {
    if (live.value && isActiveTask(live.value) && !loading.value) load()
  }, 3000)
})

// One last read when the task finishes, so the final lines show.
watch(
  () => live.value?.state,
  (state, prev) => {
    if (open.value && prev && state !== prev) load()
  },
)

onUnmounted(stopTimer)
</script>

<style scoped>
.task-log { display: flex; flex-direction: column; gap: 12px; }
.output-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.output-label { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); letter-spacing: 0.5px; }
.output {
  margin: 0; padding: 12px;
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  font-family: var(--font-mono); font-size: 11px; line-height: 1.55; color: var(--text);
  white-space: pre-wrap; word-break: break-word;
  min-height: 200px; max-height: 52vh; overflow: auto;
}
.output.muted { color: var(--text-muted); }
</style>
