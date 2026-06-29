import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { workspacesApi } from '@/api/workspaces'
import type { Workspace, WorkspaceStats } from '@/types'

const TRANSIENT = new Set(['creating', 'stopping'])
const STATS_INTERVAL = 5000

export const useWorkspacesStore = defineStore('workspaces', () => {
  const items = ref<Workspace[]>([])
  const stats = ref<Record<number, WorkspaceStats>>({})
  const loading = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let statsTimer: ReturnType<typeof setInterval> | null = null

  const hasTransient = computed(() => items.value.some(ws => TRANSIENT.has(ws.status)))
  const hasRunning = computed(() => items.value.some(ws => ws.status === 'running'))

  async function fetch() {
    loading.value = true
    try {
      items.value = await workspacesApi.list()
    } finally {
      loading.value = false
    }
    schedulePoll()
    fetchStats()
    scheduleStats()
  }

  async function fetchStats() {
    if (!hasRunning.value) {
      stats.value = {}
      return
    }
    try {
      stats.value = await workspacesApi.stats()
    } catch {
      // Stats are best-effort; keep the last values on a transient failure.
    }
  }

  function scheduleStats() {
    if (statsTimer) clearInterval(statsTimer)
    statsTimer = setInterval(() => {
      if (hasRunning.value) fetchStats()
    }, STATS_INTERVAL)
  }

  function schedulePoll() {
    if (pollTimer) clearInterval(pollTimer)
    if (hasTransient.value) {
      pollTimer = setInterval(async () => {
        items.value = await workspacesApi.list()
        if (!hasTransient.value) {
          clearInterval(pollTimer!)
          pollTimer = null
        }
      }, 3000)
    }
  }

  async function launch(payload: { name: string; image_id: number; workspace_type: string; zone_id?: number; target_url?: string; kiosk?: boolean; kiosk_dark?: boolean; kiosk_menu?: boolean; use_tailscale?: boolean; use_gluetun?: boolean; ephemeral?: boolean; lan_access?: boolean; ts_exit_node?: string; ts_accept_routes?: boolean; ts_accept_dns?: boolean; custom_dns?: boolean; dns_servers?: string; install_packages?: string; proot_apps?: string; appimages?: string; allow_sudo?: boolean; inject_ssh_key?: boolean; pixelflux_wayland?: boolean }) {
    const ws = await workspacesApi.create(payload)
    items.value.unshift(ws)
    schedulePoll()
    return ws
  }

  async function update(
    id: number,
    payload: {
      name?: string
      target_url?: string
      kiosk?: boolean
      kiosk_dark?: boolean
      kiosk_menu?: boolean
      use_tailscale?: boolean
      use_gluetun?: boolean
      ephemeral?: boolean
      lan_access?: boolean
      ts_exit_node?: string
      ts_accept_routes?: boolean
      ts_accept_dns?: boolean
      custom_dns?: boolean
      dns_servers?: string
      install_packages?: string
      proot_apps?: string
      appimages?: string
      allow_sudo?: boolean
      inject_ssh_key?: boolean
      pixelflux_wayland?: boolean
    },
  ) {
    const ws = await workspacesApi.update(id, payload)
    const idx = items.value.findIndex(w => w.id === id)
    if (idx !== -1) items.value[idx] = ws
    return ws
  }

  async function stop(id: number) {
    const ws = await workspacesApi.stop(id)
    const idx = items.value.findIndex(w => w.id === id)
    if (idx !== -1) items.value[idx] = ws
    schedulePoll()
  }

  async function start(id: number) {
    const ws = await workspacesApi.start(id)
    const idx = items.value.findIndex(w => w.id === id)
    if (idx !== -1) items.value[idx] = ws
    schedulePoll()
  }

  async function remove(id: number, purgeStorage = false) {
    await workspacesApi.remove(id, purgeStorage)
    items.value = items.value.filter(w => w.id !== id)
  }

  async function clone(id: number, payload: { name: string; image_id?: number }) {
    const ws = await workspacesApi.clone(id, payload)
    items.value.unshift(ws)
    schedulePoll()
    return ws
  }

  async function migrate(id: number, payload: { zone_id: number }) {
    const ws = await workspacesApi.migrate(id, payload)
    const idx = items.value.findIndex(w => w.id === id)
    if (idx !== -1) items.value[idx] = ws
    schedulePoll()
    return ws
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    if (statsTimer) {
      clearInterval(statsTimer)
      statsTimer = null
    }
  }

  return { items, stats, loading, fetch, fetchStats, launch, update, stop, start, remove, clone, migrate, stopPolling }
})
