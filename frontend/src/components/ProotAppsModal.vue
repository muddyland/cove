<template>
  <BaseModal v-model="open" :title="`Apps · ${ws.name}`" width="760px">
    <div class="apps">
      <div class="toolbar">
        <span class="summary">
          <template v-if="state">
            {{ installedCount }} installed
            <template v-if="updatable.length"> · <span class="upd">{{ updatable.length }} update{{ updatable.length === 1 ? '' : 's' }}</span></template>
          </template>
        </span>
        <div class="toolbar-actions">
          <NeonButton variant="ghost" :loading="loading" @click="load">
            <RefreshCw :size="13" /> Refresh
          </NeonButton>
          <NeonButton
            v-if="updatable.length"
            variant="primary"
            :loading="starting === 'update-all'"
            :disabled="!!starting"
            @click="start('update', updatable, 'update-all')"
          ><ArrowUpCircle :size="13" /> Update all</NeonButton>
        </div>
      </div>

      <p v-if="error" class="note error">{{ error }}</p>
      <p v-else-if="state && !state.available" class="note">
        proot-apps isn't available in this workspace's image.
      </p>
      <p v-if="checkNote" class="note">{{ checkNote }}</p>

      <div v-if="!state && loading" class="note">Loading…</div>
      <div v-else-if="state && !state.apps.length" class="note">No proot-apps installed yet.</div>
      <ul v-else-if="state" class="app-list">
        <li v-for="app in state.apps" :key="app.name ?? app.folder" class="app-row">
          <div class="app-main">
            <span class="app-name">{{ app.name ?? app.folder }}</span>
            <span class="chip" :class="statusOf(app).cls" :title="digestTitle(app)">{{ statusOf(app).label }}</span>
            <span v-if="app.name && !app.in_config && app.installed" class="chip muted" title="Not in this workspace's saved app list, so it isn't reinstalled after a migration">not saved</span>
          </div>
          <div v-if="app.name" class="app-actions">
            <template v-if="busy.has(app.name)">
              <span class="busy"><Loader2 :size="13" class="spin" /> {{ busy.get(app.name) }}</span>
            </template>
            <template v-else>
              <NeonButton
                v-if="app.update_available"
                variant="primary"
                :disabled="!!starting"
                :loading="starting === `update:${app.name}`"
                @click="start('update', [app.name], `update:${app.name}`)"
              ><ArrowUpCircle :size="13" /> Update</NeonButton>
              <NeonButton
                v-if="!app.installed && !app.downloading"
                variant="success"
                :disabled="!!starting"
                :loading="starting === `install:${app.name}`"
                @click="start('install', [app.name], `install:${app.name}`)"
              ><Download :size="13" /> Install</NeonButton>
              <NeonButton
                variant="ghost"
                class="remove"
                :disabled="!!starting || app.downloading"
                :title="app.downloading ? 'Wait for the download to finish' : ''"
                @click="askRemove(app.name)"
              ><Trash2 :size="13" /> Remove</NeonButton>
            </template>
          </div>
        </li>
      </ul>

      <div class="add">
        <button type="button" class="add-toggle" :aria-expanded="adding" @click="adding = !adding">
          <Plus :size="13" /> Add apps <ChevronDown :size="12" class="chev" :class="{ flip: adding }" />
        </button>
        <div v-if="adding" class="add-body">
          <ProotAppsSelect v-model="toInstall" />
          <div class="add-actions">
            <NeonButton
              variant="success"
              :disabled="!toInstall.length || !!starting"
              :loading="starting === 'install-new'"
              @click="installSelected"
            ><Download :size="13" /> Install {{ toInstall.length || '' }}</NeonButton>
          </div>
        </div>
      </div>

      <div class="tasks">
        <div class="tasks-head">
          <span class="section-label">Background tasks</span>
          <NeonButton v-if="finishedTasks.length" variant="ghost" :loading="clearing" @click="clearFinished">
            Clear finished
          </NeonButton>
        </div>
        <p v-if="!wsTasks.length" class="note">No tasks. Installs and updates run in the background — you can close this.</p>
        <ul v-else class="task-list">
          <li v-for="t in wsTasks" :key="t.id" class="task-row">
            <TaskSummary :task="t" />
            <NeonButton variant="ghost" @click="showLog(t)"><ScrollText :size="13" /> Log</NeonButton>
          </li>
        </ul>
      </div>
    </div>
  </BaseModal>

  <ConfirmModal
    v-model="confirmOpen"
    title="Remove app"
    :message="`Remove ${pendingRemove} from '${ws.name}'? Its files under ~/proot-apps are deleted and it's dropped from the saved app list.`"
    confirm-label="REMOVE"
    :loading="starting === `remove:${pendingRemove}`"
    @confirm="confirmRemove"
  />
  <TaskLogModal v-model="logOpen" :ws-id="ws.id" :task="logTask" :workspace-name="ws.name" />
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import ConfirmModal from './ConfirmModal.vue'
import ProotAppsSelect from './ProotAppsSelect.vue'
import TaskLogModal from './TaskLogModal.vue'
import TaskSummary from './TaskSummary.vue'
import { ArrowUpCircle, ChevronDown, Download, Loader2, Plus, RefreshCw, ScrollText, Trash2 } from 'lucide-vue-next'
import { prootApi } from '@/api/proot'
import { workspacesApi } from '@/api/workspaces'
import { isActiveTask, OP_ACTIVE, useTasksStore } from '@/stores/tasks'
import { useUiStore } from '@/stores/ui'
import { useWorkspacesStore } from '@/stores/workspaces'
import type { ProotApp, ProotApps, ProotTask, ProotTaskOp, Workspace } from '@/types'

const props = defineProps<{ ws: Workspace }>()
const open = defineModel<boolean>({ default: false })

const tasks = useTasksStore()
const ui = useUiStore()
const workspaces = useWorkspacesStore()

const state = ref<ProotApps | null>(null)
const loading = ref(false)
const error = ref('')
const starting = ref<string | null>(null)
const clearing = ref(false)
const adding = ref(false)
const toInstall = ref<string[]>([])
const confirmOpen = ref(false)
const pendingRemove = ref('')
const logOpen = ref(false)
const logTask = ref<ProotTask | null>(null)
let unsubscribe: (() => void) | null = null

const wsTasks = computed(() => {
  const group = tasks.groups.find(g => g.workspace_id === props.ws.id)
  return group ? [...group.tasks].reverse() : []
})
const finishedTasks = computed(() => wsTasks.value.filter(t => !isActiveTask(t)))

// App name -> what's happening to it, for apps in a queued/running task.
const busy = computed(() => {
  const map = new Map<string, string>()
  for (const t of wsTasks.value) {
    if (!isActiveTask(t)) continue
    for (const app of t.apps) {
      if (!map.has(app)) map.set(app, t.state === 'queued' ? `${t.op} queued` : `${OP_ACTIVE[t.op]}…`)
    }
  }
  return map
})

const installedCount = computed(() => state.value?.apps.filter(a => a.installed).length ?? 0)
const updatable = computed(() =>
  (state.value?.apps ?? [])
    .filter(a => a.name && a.update_available && !busy.value.has(a.name))
    .map(a => a.name as string),
)

const checkNote = computed(() => {
  const s = state.value
  if (!s || !s.available) return ''
  const checkable = s.apps.filter(a => a.name && a.installed && a.installed_digest)
  if (!checkable.length) return ''
  if (!s.checked) return "Couldn't check for updates right now — try Refresh later."
  if (checkable.some(a => a.update_available === null)) return "Couldn't check some apps for updates — try Refresh later."
  return ''
})

function statusOf(app: ProotApp): { label: string; cls: string } {
  if (app.downloading) return { label: 'downloading', cls: 'warn' }
  if (!app.installed) return { label: 'not installed', cls: 'bad' }
  if (app.update_available === true) return { label: 'update available', cls: 'accent' }
  if (app.update_available === false) return { label: 'up to date', cls: 'good' }
  return { label: 'installed', cls: 'muted' }
}

function digestTitle(app: ProotApp) {
  const short = (d: string | null) => (d ? d.replace('sha256:', '').slice(0, 12) : '—')
  if (!app.installed_digest) return ''
  return `installed ${short(app.installed_digest)} · latest ${short(app.latest_digest)}`
}

async function load() {
  loading.value = true
  try {
    state.value = await prootApi.installed(props.ws.id)
    error.value = ''
  } catch (e: any) {
    error.value = e?.message || 'Failed to load apps.'
  } finally {
    loading.value = false
  }
}

async function start(op: ProotTaskOp, apps: string[], tag: string) {
  if (!apps.length || starting.value) return
  starting.value = tag
  try {
    await prootApi.startTask(props.ws.id, op, apps)
    ui.toast(`${op} ${apps.join(', ')} started — follow it in the tasks menu`, 'info')
    tasks.kick()
    // Install/remove change the saved app list; keep the dashboard's copy in step.
    if (op !== 'update') {
      const fresh = await workspacesApi.get(props.ws.id)
      const idx = workspaces.items.findIndex(w => w.id === fresh.id)
      if (idx !== -1) workspaces.items[idx] = fresh
    }
    return true
  } catch (e: any) {
    ui.toast(e?.message || `Failed to start ${op}`, 'error')
    return false
  } finally {
    starting.value = null
  }
}

async function installSelected() {
  const installed = new Set((state.value?.apps ?? []).filter(a => a.installed).map(a => a.name))
  const apps = toInstall.value.filter(a => !installed.has(a))
  if (!apps.length) {
    ui.toast('Those apps are already installed', 'info')
    toInstall.value = []
    return
  }
  if (await start('install', apps, 'install-new')) {
    toInstall.value = []
    adding.value = false
  }
}

function askRemove(name: string) {
  pendingRemove.value = name
  confirmOpen.value = true
}

async function confirmRemove() {
  const name = pendingRemove.value
  if (await start('remove', [name], `remove:${name}`)) confirmOpen.value = false
}

async function clearFinished() {
  clearing.value = true
  try {
    await prootApi.clearTasks(props.ws.id)
    await tasks.refresh()
  } catch (e: any) {
    ui.toast(e?.message || 'Failed to clear tasks', 'error')
  } finally {
    clearing.value = false
  }
}

function showLog(t: ProotTask) {
  logTask.value = t
  logOpen.value = true
}

watch(open, value => {
  unsubscribe?.()
  unsubscribe = null
  if (!value) return
  unsubscribe = tasks.subscribe()
  state.value = null
  error.value = ''
  adding.value = false
  toInstall.value = []
  load()
})

// Reload the app list when a task finishes, so versions and chips are current.
watch(() => tasks.finishedTick, () => {
  if (open.value) load()
})

onUnmounted(() => unsubscribe?.())
</script>

<style scoped>
.apps { display: flex; flex-direction: column; gap: 14px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.toolbar-actions { display: flex; gap: 8px; }
.summary { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); letter-spacing: 0.5px; }
.summary .upd { color: var(--accent); }
.note { margin: 0; font-size: 12px; color: var(--text-muted); }
.note.error { color: var(--red); }

.app-list, .task-list {
  list-style: none; margin: 0; padding: 0;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  max-height: 34vh; overflow-y: auto;
}
.app-row, .task-row {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 8px 10px; border-bottom: 1px solid var(--border);
}
.app-row:last-child, .task-row:last-child { border-bottom: none; }
.app-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.app-name { font-family: var(--font-mono); font-size: 13px; color: var(--text); word-break: break-all; }
.app-actions { display: flex; gap: 6px; flex-shrink: 0; }
.app-actions :deep(.btn) { padding: 5px 10px; }
.remove:hover:not(:disabled) { color: var(--red); border-color: var(--red); }
.busy { display: inline-flex; align-items: center; gap: 6px; font-family: var(--font-mono); font-size: 11px; color: var(--amber); }

.chip {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: 1px; text-transform: uppercase;
  padding: 1px 6px; border: 1px solid currentColor; border-radius: var(--radius-sm); white-space: nowrap;
}
.chip.good { color: var(--green); }
.chip.accent { color: var(--accent); text-shadow: var(--glow-sm); }
.chip.warn { color: var(--amber); }
.chip.bad { color: var(--red); }
.chip.muted { color: var(--text-muted); border-color: var(--border); }

.add { display: flex; flex-direction: column; gap: 8px; }
.add-toggle {
  align-self: flex-start; display: inline-flex; align-items: center; gap: 6px;
  background: none; border: none; padding: 0; cursor: pointer;
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--accent);
}
.chev { transition: transform 0.15s; }
.chev.flip { transform: rotate(180deg); }
.add-body { display: flex; flex-direction: column; gap: 8px; }
.add-actions { display: flex; justify-content: flex-end; }

.tasks { display: flex; flex-direction: column; gap: 8px; }
.tasks-head { display: flex; align-items: center; justify-content: space-between; min-height: 30px; }
.section-label { font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted); }

.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 560px) {
  .app-row { flex-direction: column; align-items: flex-start; }
}
</style>
