import { computed, onUnmounted, ref, watch, type Ref } from 'vue'
import { workspacesApi, type StreamLease } from '@/api/workspaces'

/** How often an open stream page checks that it still holds its workspace. */
export const LEASE_CHECK_MS = 3000

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
  const movedSince = computed(() => {
    const since = movedTo.value?.since
    if (!since) return ''
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(since) ? since : `${since}Z`)
    return isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  })
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
  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisible)
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
