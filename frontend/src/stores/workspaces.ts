import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { workspacesApi } from '@/api/workspaces'
import type { Workspace } from '@/types'

const TRANSIENT = new Set(['creating', 'stopping'])

export const useWorkspacesStore = defineStore('workspaces', () => {
  const items = ref<Workspace[]>([])
  const loading = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null

  const hasTransient = computed(() => items.value.some(ws => TRANSIENT.has(ws.status)))

  async function fetch() {
    loading.value = true
    try {
      items.value = await workspacesApi.list()
    } finally {
      loading.value = false
    }
    schedulePoll()
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

  async function launch(payload: { name: string; image_id: number; workspace_type: string; target_url?: string }) {
    const ws = await workspacesApi.create(payload)
    items.value.unshift(ws)
    schedulePoll()
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

  async function remove(id: number) {
    await workspacesApi.remove(id)
    items.value = items.value.filter(w => w.id !== id)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return { items, loading, fetch, launch, stop, start, remove, stopPolling }
})
