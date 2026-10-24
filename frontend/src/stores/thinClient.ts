import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Router } from 'vue-router'
import { thinClientsApi } from '@/api/thinClients'
import type { ThinClientSelf, Workspace } from '@/types'

/**
 * Is this browser a Cove thin client (a kiosk bound to a device)? Asked once per
 * page load; on an ordinary browser the answer is a 404 and `info` stays null.
 *
 * On a kiosk the dashboard may jump straight into a workspace after sign-in
 * (the device's "auto open" policy). That happens at most once per page load,
 * so going back to the dashboard on purpose doesn't bounce you into it again.
 */
export const useThinClientStore = defineStore('thinClient', () => {
  const info = ref<ThinClientSelf | null>(null)
  let loaded: Promise<void> | null = null
  let autoOpened = false

  function load(): Promise<void> {
    if (!loaded) {
      loaded = thinClientsApi
        .self()
        .then(v => { info.value = v })
        .catch(() => { info.value = null })
    }
    return loaded
  }

  /** Pick the workspace the device policy says to open, if any. */
  function autoOpenTarget(items: Workspace[]): Workspace | null {
    const tc = info.value
    if (!tc) return null
    if (tc.auto_open === 'pinned' && tc.pinned_workspace_id) {
      return items.find(w => w.public_id === tc.pinned_workspace_id) ?? null
    }
    if (tc.auto_open === 'running') {
      const live = items.filter(w => w.status === 'running' && w.connectable)
      return live.length === 1 ? live[0] : null
    }
    return null
  }

  async function maybeAutoOpen(
    items: Workspace[],
    router: Router,
    start: (id: number) => Promise<unknown>,
  ): Promise<void> {
    if (autoOpened) return
    await load()
    if (!info.value) return
    autoOpened = true
    const ws = autoOpenTarget(items)
    if (!ws) return
    // A pinned workspace that is halted is started first (the stream page shows
    // its boot progress); one that is busy changing state is left alone.
    if (ws.status === 'stopped') {
      try { await start(ws.id) } catch { return }
    } else if (ws.status !== 'running') {
      return
    }
    router.push(`/app/workspace/${ws.id}`)
  }

  return { info, load, autoOpenTarget, maybeAutoOpen }
})
