import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { workspacesApi } from '@/api/workspaces'
import { useTasksStore } from '@/stores/tasks'
import type { Workspace, WorkspaceStats } from '@/types'

const TRANSIENT = new Set(['creating', 'stopping', 'migrating'])
// List refresh cadence. Something always polls while a view is subscribed: a
// workspace can change state with nobody in this tab touching it (it crashes,
// hits the max-runtime auto-halt, is halted from another device or by an admin,
// or its VPN sidecar fails), and the grid must not keep offering CONNECT on a
// dead node until the user reloads.
export const FAST_POLL = 3000 // while anything is starting/stopping/migrating
export const IDLE_POLL = 10000 // otherwise
const MAX_BACKOFF = 60000 // ceiling after repeated failures
const STATS_INTERVAL = 5000

function isHidden(): boolean {
  return typeof document !== 'undefined' && document.hidden
}

type CreatePayload = Parameters<typeof workspacesApi.create>[0]
type UpdatePayload = Parameters<typeof workspacesApi.update>[1]

export const useWorkspacesStore = defineStore('workspaces', () => {
  const items = ref<Workspace[]>([])
  const stats = ref<Record<number, WorkspaceStats>>({})
  const loading = ref(false)
  // Admin max runtime (hours, 0 = unlimited); null until first fetched.
  const maxRuntimeHours = ref<number | null>(null)

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let statsTimer: ReturnType<typeof setTimeout> | null = null
  let subscribers = 0
  let failures = 0
  let listening = false

  // A running workspace still waiting on its first frame counts as transient, so
  // the grid polls fast until it can be connected to.
  const hasTransient = computed(() =>
    items.value.some(ws => TRANSIENT.has(ws.status) || (ws.status === 'running' && !ws.connectable)),
  )
  const hasRunning = computed(() => items.value.some(ws => ws.status === 'running'))

  function upsert(ws: Workspace) {
    const idx = items.value.findIndex(w => w.id === ws.id)
    if (idx !== -1) items.value[idx] = ws
    else items.value.unshift(ws)
  }

  // Drop stats for workspaces that aren't running any more, so a halted or
  // crashed card never keeps showing its last CPU/MEM numbers.
  function pruneStats() {
    const running = new Set(items.value.filter(w => w.status === 'running').map(w => w.id))
    const next: Record<number, WorkspaceStats> = {}
    for (const [id, s] of Object.entries(stats.value)) {
      if (running.has(Number(id))) next[Number(id)] = s
    }
    stats.value = next
  }

  // A desktop with saved apps queues their first-boot install as it comes up.
  // Check the tasks menu then rather than at its idle cadence, so the install
  // shows up while it runs.
  function noticeBootInstalls(next: Workspace[]) {
    const before = new Map(items.value.map(w => [w.id, w.status]))
    const booted = next.some(
      w =>
        w.status === 'running' &&
        before.has(w.id) &&
        before.get(w.id) !== 'running' &&
        !!(w.proot_apps || w.appimages),
    )
    if (booted) setTimeout(() => useTasksStore().kick(), 2000)
  }

  async function refreshList(): Promise<boolean> {
    try {
      const next = await workspacesApi.list()
      noticeBootInstalls(next)
      items.value = next
      failures = 0
      pruneStats()
      return true
    } catch {
      // Offline / transient server error: keep the last known list and back
      // off. (A full outage is surfaced by the connection store's overlay.)
      failures++
      return false
    }
  }

  /** Initial / explicit load. Drives the "scanning" state on the first call. */
  async function fetch() {
    loading.value = true
    try {
      await refreshList()
    } finally {
      loading.value = false
    }
    if (maxRuntimeHours.value === null) {
      workspacesApi
        .runtimePolicy()
        .then(p => (maxRuntimeHours.value = p.max_hours))
        .catch(() => {})
    }
    if (subscribers > 0) {
      schedulePoll()
      fetchStats()
    }
  }

  /**
   * Re-read one workspace. Returns it, or null if it no longer exists (purged
   * from another tab or by an admin), in which case it's dropped from the list.
   * Throws only for non-404 failures, which callers treat as transient.
   */
  async function refreshOne(id: number): Promise<Workspace | null> {
    try {
      const ws = await workspacesApi.get(id)
      upsert(ws)
      pruneStats()
      return ws
    } catch (e: any) {
      if (e?.status === 404) {
        items.value = items.value.filter(w => w.id !== id)
        return null
      }
      throw e
    }
  }

  async function fetchStats() {
    if (!hasRunning.value) {
      stats.value = {}
      return
    }
    try {
      stats.value = await workspacesApi.stats()
      pruneStats()
    } catch {
      // Stats are best-effort; keep the last values on a transient failure.
    }
  }

  function nextPollDelay(): number {
    const base = hasTransient.value ? FAST_POLL : IDLE_POLL
    return failures ? Math.min(base * 2 ** failures, MAX_BACKOFF) : base
  }

  function schedulePoll(delay = nextPollDelay()) {
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
    if (subscribers <= 0 || isHidden()) return
    pollTimer = setTimeout(async () => {
      pollTimer = null
      await refreshList()
      schedulePoll()
    }, delay)
  }

  function scheduleStats() {
    if (statsTimer) clearTimeout(statsTimer)
    statsTimer = null
    if (subscribers <= 0 || isHidden()) return
    statsTimer = setTimeout(async () => {
      statsTimer = null
      if (hasRunning.value) await fetchStats()
      scheduleStats()
    }, STATS_INTERVAL)
  }

  function clearTimers() {
    if (pollTimer) clearTimeout(pollTimer)
    if (statsTimer) clearTimeout(statsTimer)
    pollTimer = statsTimer = null
  }

  // Hidden tabs stop polling entirely; coming back refreshes at once so the grid
  // is never stale when you look at it.
  async function onVisibility() {
    if (isHidden()) {
      clearTimers()
      return
    }
    if (subscribers <= 0) return
    await refreshList()
    schedulePoll()
    fetchStats()
    scheduleStats()
  }

  /** Start background refresh. Ref-counted: views call it on mount and pair it
   *  with stopPolling() on unmount; timers stop with the last subscriber. */
  function startPolling() {
    subscribers++
    if (!listening && typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibility)
      listening = true
    }
    if (subscribers === 1) {
      schedulePoll()
      scheduleStats()
    }
  }

  function stopPolling() {
    subscribers = Math.max(0, subscribers - 1)
    if (subscribers > 0) return
    clearTimers()
    if (listening) {
      document.removeEventListener('visibilitychange', onVisibility)
      listening = false
    }
  }

  // After any action that starts a transition, poll fast right away.
  function kick() {
    if (subscribers > 0) schedulePoll(FAST_POLL)
  }

  async function launch(payload: CreatePayload) {
    const ws = await workspacesApi.create(payload)
    items.value.unshift(ws)
    kick()
    return ws
  }

  async function update(id: number, payload: UpdatePayload) {
    const ws = await workspacesApi.update(id, payload)
    upsert(ws)
    return ws
  }

  async function stop(id: number) {
    upsert(await workspacesApi.stop(id))
    pruneStats()
    kick()
  }

  async function start(id: number) {
    upsert(await workspacesApi.start(id))
    kick()
  }

  /** Push back the max-runtime auto-halt of a running workspace. */
  async function extend(id: number) {
    const ws = await workspacesApi.extend(id)
    upsert(ws)
    return ws
  }

  async function remove(id: number, purgeStorage = false) {
    await workspacesApi.remove(id, purgeStorage)
    items.value = items.value.filter(w => w.id !== id)
    pruneStats()
  }

  async function clone(id: number, payload: { name: string; image_id?: number }) {
    const ws = await workspacesApi.clone(id, payload)
    items.value.unshift(ws)
    kick()
    return ws
  }

  async function migrate(id: number, payload: { zone_id: number }) {
    const ws = await workspacesApi.migrate(id, payload)
    upsert(ws)
    kick()
    return ws
  }

  return {
    items,
    stats,
    loading,
    maxRuntimeHours,
    hasTransient,
    fetch,
    refreshOne,
    fetchStats,
    launch,
    update,
    stop,
    start,
    extend,
    remove,
    clone,
    migrate,
    startPolling,
    stopPolling,
  }
})
