import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * Whether the Cove backend is reachable from this browser.
 *
 * Deliberately not `navigator.onLine`: the laptop is usually perfectly online
 * while the server it talks to is down, restarting after an update, or behind a
 * tunnel that dropped — which is the case this covers. The signal comes from
 * api/client.ts, the one place every request funnels through, and the rule there
 * is simple: a `fetch` that *rejects* means no response at all (DNS, TCP, TLS,
 * server down), while any HTTP status — 500 included — means the backend
 * answered and is therefore up.
 *
 * While offline the store polls /api/health on a widening interval and clears
 * itself the moment one succeeds, so recovery needs no action from the user: the
 * views' own polling loops resume against a server that is answering again.
 */

// Widening gaps between automatic retries, in seconds. Starts quick because the
// common case is a server restart that's back within a few seconds, then backs
// off so a long outage isn't a request every 3s for an hour.
const RETRY_STEPS = [3, 5, 8, 15]

export const useConnectionStore = defineStore('connection', () => {
  const offline = ref(false)
  /** A probe is in flight — drives the RETRY button's spinner. */
  const checking = ref(false)
  /** Seconds until the next automatic retry (0 while one is running). */
  const retryIn = ref(0)

  let ticker: ReturnType<typeof setInterval> | null = null
  let attempt = 0

  function stopTicker() {
    if (ticker) clearInterval(ticker)
    ticker = null
    retryIn.value = 0
  }

  /** Is the server answering? Any response counts — only a dead connection doesn't. */
  async function probe(): Promise<boolean> {
    if (checking.value) return false
    checking.value = true
    try {
      await fetch('/api/health', { cache: 'no-store' })
      markOnline()
      return true
    } catch {
      return false
    } finally {
      checking.value = false
    }
  }

  function scheduleRetry() {
    stopTicker()
    retryIn.value = RETRY_STEPS[Math.min(attempt, RETRY_STEPS.length - 1)]
    ticker = setInterval(async () => {
      if (retryIn.value > 0) {
        retryIn.value -= 1
        return
      }
      stopTicker()
      attempt += 1
      if (!(await probe()) && offline.value) scheduleRetry()
    }, 1000)
  }

  /** A request got no response at all. */
  function markOffline() {
    if (offline.value) return
    offline.value = true
    attempt = 0
    scheduleRetry()
  }

  /** The server answered. */
  function markOnline() {
    if (!offline.value) return
    offline.value = false
    attempt = 0
    stopTicker()
  }

  /** Retry now, from the overlay's button. */
  async function retryNow() {
    stopTicker()
    if (!(await probe()) && offline.value) scheduleRetry()
  }

  // The machine getting its network back is the best possible hint that the
  // server may be reachable again — probe straight away rather than sitting out
  // the remaining countdown.
  if (typeof window !== 'undefined') {
    window.addEventListener('online', () => {
      if (offline.value) retryNow()
    })
  }

  return { offline, checking, retryIn, markOffline, markOnline, retryNow }
})
