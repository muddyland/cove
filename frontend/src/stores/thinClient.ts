import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Router } from 'vue-router'
import { thinClientsApi } from '@/api/thinClients'
import { useAuthStore } from '@/stores/auth'
import type { ThinClientSelf, Workspace } from '@/types'

/**
 * Is this browser a Cove thin client (a kiosk bound to a device)? Asked once per
 * page load; on an ordinary browser the answer is a 404 and `info` stays null.
 *
 * It also decides whether the dashboard jumps straight into a workspace:
 * - the account's own sign-in workspace (Preferences, or set by an admin), on
 *   any browser, right after signing in;
 * - otherwise, on a kiosk, the device's "after sign-in" policy.
 * On a kiosk that's checked once per page load (the kiosk reloads on sign-out),
 * elsewhere once per sign-in, so going back to the dashboard on purpose doesn't
 * bounce you into the workspace again.
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

  /** Pick the workspace to open: the account's sign-in workspace when it
   *  still exists, else what the device policy says (on a kiosk), if any. */
  function autoOpenTarget(items: Workspace[], userChoice: string | null = null): Workspace | null {
    if (userChoice) {
      const own = items.find(w => w.public_id === userChoice)
      if (own) return own
    }
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
    const auth = useAuthStore()
    const fresh = auth.takeFreshSignIn()
    await load()
    const kiosk = !!info.value
    if (!fresh && (!kiosk || autoOpened)) return
    autoOpened = true
    const ws = autoOpenTarget(items, auth.user?.auto_launch_workspace_id ?? null)
    if (!ws) return
    // A halted workspace is started first (the stream page shows its boot
    // progress); one that is busy changing state is left alone.
    if (ws.status === 'stopped') {
      try { await start(ws.id) } catch { return }
    } else if (ws.status !== 'running') {
      return
    }
    router.push(`/app/workspace/${ws.id}`)
  }

  return { info, load, autoOpenTarget, maybeAutoOpen }
})
