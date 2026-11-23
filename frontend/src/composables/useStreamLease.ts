import { computed, onUnmounted, ref, watch, type Ref } from 'vue'
import { workspacesApi, type StreamLease } from '@/api/workspaces'

/** How often an open stream page checks that it still holds its workspace. */
export const LEASE_CHECK_MS = 3000

/** A lease's `since` as a local time of day ("14:05"), or ''. */
export function leaseTime(since: string | null | undefined): string {
  if (!since) return ''
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(since) ? since : `${since}Z`)
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function newViewerId(): string {
  try { return crypto.randomUUID() } catch { /* insecure context */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

/**
 * Selkies serves one controlling screen per workspace: opening it somewhere else
 * (another browser, a thin client) disconnects this page's stream, which the page
 * can't see from inside the (often cross-origin) iframe. So the page claims the
 * workspace with Cove when its stream loads (`streamUrl` set) and checks every few
 * seconds whether it still holds it. When it doesn't, `streamUrl` is cleared
 * (dropping the dead stream, which also stops it fighting the new screen) and
 * `movedTo` says where the workspace went.
 */
export function useStreamLease(wsId: Readonly<Ref<number>>, streamUrl: Ref<string | null>) {
  const viewerId = newViewerId()
  const movedTo = ref<Pick<StreamLease, 'label' | 'since'> | null>(null)
  const movedSince = computed(() => leaseTime(movedTo.value?.since))
  let timer: ReturnType<typeof setInterval> | null = null
  let heldWs: number | null = null

  function stop(release: boolean) {
    if (timer) { clearInterval(timer); timer = null }
    if (release && heldWs !== null) workspacesApi.releaseStream(heldWs, viewerId).catch(() => {})
    heldWs = null
  }

  async function check() {
    const id = heldWs
    if (id === null) return
    let lease: StreamLease
    try {
      lease = await workspacesApi.checkStream(id, viewerId)
    } catch {
      return  // transient: the next check will tell
    }
    if (id !== heldWs || lease.holder) return
    stop(false)
    movedTo.value = { label: lease.label, since: lease.since }
    streamUrl.value = null
  }

  watch(streamUrl, async url => {
    if (!url) {
      // Our own teardown (stopped, retry, switched workspace): let it go. When
      // the stream moved elsewhere the lease was already dropped, unreleased.
      stop(true)
      return
    }
    const id = wsId.value
    try {
      await workspacesApi.claimStream(id, viewerId)
    } catch {
      return  // can't tell who holds it; the stream itself still works
    }
    if (id !== wsId.value || streamUrl.value !== url) return
    movedTo.value = null
    heldWs = id
    if (!timer) timer = setInterval(check, LEASE_CHECK_MS)
  })

  // Background tabs check rarely (timers are throttled): check on return.
  function onVisible() { if (document.visibilityState === 'visible') check() }
  document.addEventListener('visibilitychange', onVisible)
  // Closing the tab, reloading or leaving Cove doesn't unmount anything: let
  // go then too (keepalive outlives the page), or the workspace reads as in
  // use by this page for the lease's TTL and the next page to open it is
  // warned about a screen that's already gone. Back from the bfcache, the
  // next check re-claims it.
  function onPageHide() {
    if (heldWs !== null) workspacesApi.releaseStream(heldWs, viewerId, true).catch(() => {})
  }
  window.addEventListener('pagehide', onPageHide)
  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisible)
    window.removeEventListener('pagehide', onPageHide)
    stop(true)
  })

  return {
    viewerId,
    movedTo,
    movedSince,
    /** Clear the message (taking the workspace back, or it stopped). */
    dismissMoved: () => { movedTo.value = null },
    check,
  }
}
