<template>
  <div ref="root" class="tasks-dropdown" :class="{ open: isOpen }">
    <button
      type="button"
      class="icon-link tasks-trigger"
      :class="{ active: isOpen, busy: tasks.activeCount > 0 }"
      :title="triggerTitle"
      :aria-label="triggerTitle"
      :aria-expanded="isOpen"
      @click="toggle"
    >
      <component :is="tasks.activeCount > 0 ? Loader2 : ListChecks" :size="17" :class="{ spin: tasks.activeCount > 0 }" />
      <span v-if="tasks.activeCount > 0" class="badge">{{ tasks.activeCount }}</span>
    </button>

    <div v-show="isOpen" class="tasks-menu" role="dialog" aria-label="Background tasks">
      <div class="menu-head">
        <span class="menu-title">Background tasks</span>
      </div>
      <LoadingSpinner v-if="!tasks.loaded" block :size="16" />
      <p v-else-if="!tasks.groups.length" class="empty">No background tasks. App installs and updates show up here.</p>
      <div v-for="g in tasks.groups" :key="g.workspace_id" class="group">
        <div class="group-head">
          <span class="ws-name">{{ g.workspace_name }}</span>
          <button
            v-if="g.tasks.some(t => !isActiveTask(t))"
            type="button"
            class="link-btn"
            :disabled="clearing === g.workspace_id"
            @click="clear(g.workspace_id)"
          >Clear finished</button>
        </div>
        <ul class="task-list">
          <li v-for="t in [...g.tasks].reverse()" :key="t.id" class="task-row">
            <TaskSummary :task="t" />
            <button type="button" class="link-btn" @click="showLog(g, t)"><ScrollText :size="12" /> Log</button>
          </li>
        </ul>
      </div>
    </div>

    <TaskLogModal v-model="logOpen" :ws-id="logWsId" :task="logTask" :workspace-name="logWsName" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ListChecks, Loader2, ScrollText } from 'lucide-vue-next'
import TaskSummary from './TaskSummary.vue'
import LoadingSpinner from './LoadingSpinner.vue'
import TaskLogModal from './TaskLogModal.vue'
import { prootApi } from '@/api/proot'
import { isActiveTask, useTasksStore } from '@/stores/tasks'
import { useUiStore } from '@/stores/ui'
import type { ProotTask, WorkspaceProotTasks } from '@/types'

const tasks = useTasksStore()
const ui = useUiStore()

const isOpen = ref(false)
const root = ref<HTMLElement | null>(null)
const clearing = ref<number | null>(null)
const logOpen = ref(false)
const logTask = ref<ProotTask | null>(null)
const logWsId = ref(0)
const logWsName = ref('')

const triggerTitle = computed(() =>
  tasks.activeCount > 0
    ? `${tasks.activeCount} background task${tasks.activeCount === 1 ? '' : 's'} running`
    : 'Background tasks',
)

function toggle() {
  isOpen.value = !isOpen.value
  if (isOpen.value) tasks.refresh()
}

function close() {
  isOpen.value = false
}

async function clear(wsId: number) {
  clearing.value = wsId
  try {
    await prootApi.clearTasks(wsId)
    await tasks.refresh()
  } catch (e: any) {
    ui.toast(e?.message || 'Failed to clear tasks', 'error')
  } finally {
    clearing.value = null
  }
}

function showLog(g: WorkspaceProotTasks, t: ProotTask) {
  logWsId.value = g.workspace_id
  logWsName.value = g.workspace_name
  logTask.value = t
  logOpen.value = true
  isOpen.value = false
}

function onDocClick(e: MouseEvent) {
  if (isOpen.value && root.value && !root.value.contains(e.target as Node)) close()
}

let unsubscribe: (() => void) | null = null
onMounted(() => {
  document.addEventListener('click', onDocClick)
  unsubscribe = tasks.subscribe()
})
onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  unsubscribe?.()
})

defineExpose({ close })
</script>

<style scoped>
.tasks-dropdown { position: relative; }

.icon-link {
  display: inline-flex; align-items: center; justify-content: center;
  width: 30px; height: 30px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text-muted); background: var(--surface-2);
  padding: 0; cursor: pointer; transition: all 0.15s;
  position: relative;
}
.icon-link:hover { color: var(--accent); border-color: var(--accent); }
.icon-link.active { color: var(--accent); border-color: var(--accent); box-shadow: var(--glow-sm); }
.icon-link.busy { color: var(--amber); border-color: color-mix(in srgb, var(--amber) 55%, var(--border)); }

.badge {
  position: absolute; top: -6px; right: -6px;
  min-width: 16px; height: 16px; padding: 0 4px;
  border-radius: 8px; background: var(--amber); color: var(--bg);
  font-family: var(--font-mono); font-size: 10px; font-weight: 700; line-height: 16px; text-align: center;
}

.tasks-menu {
  position: absolute; top: calc(100% + 6px); right: 0;
  width: min(380px, calc(100vw - 32px));
  max-height: min(70vh, 520px); overflow-y: auto;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
  box-shadow: var(--glow-sm), var(--shadow);
  padding: 8px; z-index: 200;
  display: flex; flex-direction: column; gap: 10px;
}
.menu-head { display: flex; align-items: center; justify-content: space-between; padding: 2px 4px; }
.menu-title, .ws-name {
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  letter-spacing: 1px; text-transform: uppercase;
}
.menu-title { color: var(--accent); }
.ws-name { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { margin: 0; padding: 6px 4px 8px; font-size: 12px; color: var(--text-muted); }

.group { display: flex; flex-direction: column; gap: 4px; }
.group-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 0 4px; }
.task-list { list-style: none; margin: 0; padding: 0; border: 1px solid var(--border); border-radius: var(--radius-sm); }
.task-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 7px 8px; border-bottom: 1px solid var(--border);
}
.task-row:last-child { border-bottom: none; }

.link-btn {
  display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
  background: none; border: none; padding: 2px 4px; cursor: pointer;
  font-family: var(--font-mono); font-size: 10px; letter-spacing: 1px; text-transform: uppercase;
  color: var(--text-muted); transition: color 0.15s;
}
.link-btn:hover:not(:disabled) { color: var(--accent); }
.link-btn:disabled { opacity: 0.4; cursor: default; }

.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 860px) {
  /* In the mobile drawer, float the list across the screen width instead of
     anchoring it to the small trigger. */
  .tasks-menu {
    position: fixed; top: calc(60px + env(safe-area-inset-top)); left: 16px; right: 16px;
    width: auto; max-height: 70vh;
  }
}
</style>
