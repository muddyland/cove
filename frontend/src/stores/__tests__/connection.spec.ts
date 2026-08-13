import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useConnectionStore } from '@/stores/connection'

describe('connection store', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('starts online', () => {
    expect(useConnectionStore().offline).toBe(false)
  })

  it('goes offline and schedules a retry', () => {
    const conn = useConnectionStore()
    conn.markOffline()
    expect(conn.offline).toBe(true)
    expect(conn.retryIn).toBeGreaterThan(0)
  })

  it('clears itself when a probe reaches the server', async () => {
    const conn = useConnectionStore()
    conn.markOffline()
    fetchMock.mockResolvedValueOnce({ ok: true } as Response)

    await conn.retryNow()

    expect(fetchMock).toHaveBeenCalledWith('/api/health', { cache: 'no-store' })
    expect(conn.offline).toBe(false)
    expect(conn.retryIn).toBe(0)
  })

  it('treats any HTTP status as reachable', async () => {
    // A 500 from the backend still proves it is up — that is a different problem
    // from "there is no server to talk to", and the takeover must not claim it.
    const conn = useConnectionStore()
    conn.markOffline()
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 } as Response)

    await conn.retryNow()

    expect(conn.offline).toBe(false)
  })

  it('stays offline and re-arms when the probe fails', async () => {
    const conn = useConnectionStore()
    conn.markOffline()
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    await conn.retryNow()

    expect(conn.offline).toBe(true)
    expect(conn.retryIn).toBeGreaterThan(0)
  })

  it('counts down and probes on its own', async () => {
    const conn = useConnectionStore()
    conn.markOffline()
    const first = conn.retryIn
    fetchMock.mockResolvedValue({ ok: true } as Response)

    await vi.advanceTimersByTimeAsync((first + 1) * 1000)

    expect(fetchMock).toHaveBeenCalled()
    expect(conn.offline).toBe(false)
  })

  it('backs off across repeated failures', async () => {
    // A server that has been down for a while should not be polled every 3s.
    const conn = useConnectionStore()
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    conn.markOffline()
    const first = conn.retryIn

    for (let i = 0; i < 4; i++) {
      await vi.advanceTimersByTimeAsync((conn.retryIn + 1) * 1000)
    }

    expect(conn.retryIn).toBeGreaterThan(first)
  })

  it('marking offline twice does not restart the countdown from scratch', () => {
    const conn = useConnectionStore()
    conn.markOffline()
    vi.advanceTimersByTime(2000)
    const remaining = conn.retryIn
    conn.markOffline()
    expect(conn.retryIn).toBe(remaining)
  })
})
