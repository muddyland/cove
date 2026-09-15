import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { prootApi } from '@/api/proot'
import { useUiStore } from '@/stores/ui'
import type { ProotTask, ProotTaskOp, WorkspaceProotTasks } from '@/types'

// Poll quickly only while something is in flight; otherwise just often enough
// to notice a task started elsewhere (another tab, or a workspace's boot).
const ACTIVE_INTERVAL = 4000
const IDLE_INTERVAL = 30000

export const OP_ACTIVE: Record<ProotTaskOp, string> = {
  install: 'installing',
  update: 'updating',
  remove: 'removing',
}

export function isActiveTask(t: ProotTask) {
  return t.state === 'queued' || t.state === 'running'
}

/**
 * Background app tasks (proot-apps install/update/remove) across the user's
 * running workspaces, for the navbar. Polls with a self-rescheduling timeout so
 * a slow response never overlaps the next request, and pauses while the tab is
 * hidden.
 */
export const useTasksStore = defineStore('tasks', () => {
  const groups = ref<WorkspaceProotTasks[]>([])
  const loaded = ref(false)
  // Bumped whenever a task this page saw in flight finishes, so views showing
  // installed apps know to reload.
  const finishedTick = ref(0)

  let subscribers = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let inFlight = false
  const seenActive = new Set<string>()

  const activeCount = computed(() =>
    groups.value.reduce((n, g) => n + g.tasks.filter(isActiveTask).length, 0),
  )
  const total = computed(() => groups.value.reduce((n, g) => n + g.tasks.length, 0))

  function key(wsId: number, t: ProotTask) {
    return `${wsId}:${t.id}`
  }

  function notifyFinished(next: WorkspaceProotTasks[]) {
    const ui = useUiStore()
    let finished = false
    for (const g of next) {
      for (const t of g.tasks) {
        const k = key(g.workspace_id, t)
        if (isActiveTask(t)) {
          seenActive.add(k)
        } else if (seenActive.delete(k)) {
          finished = true
          const apps = t.apps.join(', ')
          if (t.state === 'done') ui.toast(`${g.workspace_name}: ${t.op} ${apps} finished`, 'success')
          else ui.toast(`${g.workspace_name}: ${t.op} ${apps} ${t.state}`, 'error')
        }
      }
    }
    if (finished) finishedTick.value++
  }

  async function refresh() {
    if (inFlight) return
    inFlight = true
    try {
      const next = await prootApi.allTasks()
      notifyFinished(next)
      groups.value = next
      loaded.value = true
    } catch {
      // Best-effort: keep the last list through a transient failure.
    } finally {
      inFlight = false
    }
  }

  function schedule(delay: number) {
    if (timer) clearTimeout(timer)
    timer = subscribers > 0 ? setTimeout(tick, delay) : null
  }

  async function tick() {
    timer = null
    if (typeof document !== 'undefined' && document.hidden) {
      schedule(IDLE_INTERVAL)
      return
    }
    await refresh()
    schedule(activeCount.value > 0 ? ACTIVE_INTERVAL : IDLE_INTERVAL)
  }

  function onVisible() {
    if (!document.hidden && subscribers > 0) schedule(0)
  }

  /** Start polling (ref-counted); returns the matching stop function. */
  function subscribe() {
    subscribers++
    if (subscribers === 1) {
      document.addEventListener('visibilitychange', onVisible)
      schedule(0)
    }
    let done = false
    return () => {
      if (done) return
      done = true
      subscribers--
      if (subscribers === 0) {
        document.removeEventListener('visibilitychange', onVisible)
        schedule(0)
      }
    }
  }

  /** Refresh now and keep polling at the active rate (a task was just started). */
  function kick() {
    if (subscribers > 0) schedule(0)
    else refresh()
  }

  return { groups, loaded, finishedTick, activeCount, total, refresh, subscribe, kick }
})
