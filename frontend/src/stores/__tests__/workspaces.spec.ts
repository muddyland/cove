import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { Workspace } from '@/types'

vi.mock('@/api/workspaces', () => ({
  workspacesApi: {
    list: vi.fn(),
    stats: vi.fn(),
    get: vi.fn(),
    runtimePolicy: vi.fn(),
    stop: vi.fn(),
  },
}))

const kickMock = vi.fn()
vi.mock('@/stores/tasks', () => ({ useTasksStore: () => ({ kick: kickMock }) }))

import { workspacesApi } from '@/api/workspaces'
import { useWorkspacesStore, FAST_POLL, IDLE_POLL } from '@/stores/workspaces'

const ws = (over: Partial<Workspace>) => ({ id: 1, status: 'running', connectable: true, ...over }) as Workspace

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('workspaces store polling', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    vi.mocked(workspacesApi.list).mockReset()
    vi.mocked(workspacesApi.get).mockReset()
    vi.mocked(workspacesApi.stats).mockReset().mockResolvedValue({})
    vi.mocked(workspacesApi.runtimePolicy).mockReset().mockResolvedValue({ max_hours: 24 })
    setHidden(false)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('polls fast while a workspace is still becoming connectable, then slows down', async () => {
    vi.mocked(workspacesApi.list)
      .mockResolvedValueOnce([ws({ connectable: false })])
      .mockResolvedValueOnce([ws({ connectable: false })])
      .mockResolvedValue([ws({ connectable: true })])
    const store = useWorkspacesStore()
    store.startPolling()
    await store.fetch()
    expect(workspacesApi.list).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(FAST_POLL)
    expect(store.items[0].connectable).toBe(false)
    await vi.advanceTimersByTimeAsync(FAST_POLL)
    expect(store.items[0].connectable).toBe(true)

    // Settled: no fast polling, but the idle poll keeps going.
    const calls = vi.mocked(workspacesApi.list).mock.calls.length
    await vi.advanceTimersByTimeAsync(IDLE_POLL - 1)
    expect(workspacesApi.list).toHaveBeenCalledTimes(calls)
    await vi.advanceTimersByTimeAsync(1)
    expect(workspacesApi.list).toHaveBeenCalledTimes(calls + 1)
    store.stopPolling()
  })

  it('notices a workspace that died with nobody touching it', async () => {
    vi.mocked(workspacesApi.list)
      .mockResolvedValueOnce([ws({})])
      .mockResolvedValue([ws({ status: 'error', connectable: false, error_message: 'Container exited' })])
    vi.mocked(workspacesApi.stats).mockResolvedValue({ 1: { cpu_pct: 3, mem_used: 1, mem_limit: 2, mem_pct: 50 } })
    const store = useWorkspacesStore()
    store.startPolling()
    await store.fetch()
    expect(store.stats[1]).toBeTruthy()

    await vi.advanceTimersByTimeAsync(IDLE_POLL)
    expect(store.items[0].status).toBe('error')
    // Stale CPU/MEM of the dead node are dropped.
    expect(store.stats[1]).toBeUndefined()
    store.stopPolling()
  })

  it('pauses while the tab is hidden and refreshes on return', async () => {
    vi.mocked(workspacesApi.list).mockResolvedValue([ws({})])
    const store = useWorkspacesStore()
    store.startPolling()
    await store.fetch()
    setHidden(true)
    await vi.advanceTimersByTimeAsync(IDLE_POLL * 5)
    expect(workspacesApi.list).toHaveBeenCalledTimes(1)
    setHidden(false)
    await vi.advanceTimersByTimeAsync(0)
    expect(workspacesApi.list).toHaveBeenCalledTimes(2)
    store.stopPolling()
  })

  it('survives a failing poll and backs off', async () => {
    vi.mocked(workspacesApi.list)
      .mockResolvedValueOnce([ws({})])
      .mockRejectedValueOnce(new Error('blip'))
      .mockResolvedValue([ws({ name: 'after' })])
    const store = useWorkspacesStore()
    store.startPolling()
    await store.fetch()
    await vi.advanceTimersByTimeAsync(IDLE_POLL) // fails, list kept
    expect(store.items).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(IDLE_POLL) // still backing off (2x)
    expect(workspacesApi.list).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(IDLE_POLL)
    expect(workspacesApi.list).toHaveBeenCalledTimes(3)
    expect(store.items[0].name).toBe('after')
    store.stopPolling()
  })

  it('does not poll without a subscriber', async () => {
    vi.mocked(workspacesApi.list).mockResolvedValue([ws({ status: 'creating' })])
    const store = useWorkspacesStore()
    await store.fetch()
    await vi.advanceTimersByTimeAsync(IDLE_POLL * 3)
    expect(workspacesApi.list).toHaveBeenCalledTimes(1)
  })

  it('refreshOne drops a workspace that was deleted elsewhere', async () => {
    vi.mocked(workspacesApi.list).mockResolvedValue([ws({ id: 1 }), ws({ id: 2 })])
    const store = useWorkspacesStore()
    await store.fetch()
    vi.mocked(workspacesApi.get).mockRejectedValue(Object.assign(new Error('gone'), { status: 404 }))
    expect(await store.refreshOne(2)).toBeNull()
    expect(store.items.map(w => w.id)).toEqual([1])
    vi.mocked(workspacesApi.get).mockRejectedValue(Object.assign(new Error('blip'), { status: 0 }))
    await expect(store.refreshOne(1)).rejects.toThrow('blip')
    expect(store.items).toHaveLength(1)
  })

  it('loads the runtime policy once', async () => {
    vi.mocked(workspacesApi.list).mockResolvedValue([])
    const store = useWorkspacesStore()
    await store.fetch()
    await vi.advanceTimersByTimeAsync(0)
    expect(store.maxRuntimeHours).toBe(24)
    await store.fetch()
    expect(workspacesApi.runtimePolicy).toHaveBeenCalledTimes(1)
  })
})

describe('first-boot app installs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    kickMock.mockReset()
    vi.mocked(workspacesApi.list).mockReset()
    vi.mocked(workspacesApi.stats).mockReset().mockResolvedValue({})
    vi.mocked(workspacesApi.runtimePolicy).mockReset().mockResolvedValue({ max_hours: 24 })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('checks the tasks menu when a desktop with saved apps comes up', async () => {
    const store = useWorkspacesStore()
    vi.mocked(workspacesApi.list)
      .mockResolvedValueOnce([ws({ status: 'creating', proot_apps: 'firefox' }), ws({ id: 2, status: 'creating' })])
      .mockResolvedValueOnce([ws({ status: 'running', proot_apps: 'firefox' }), ws({ id: 2, status: 'running' })])
      .mockResolvedValueOnce([ws({ status: 'running', proot_apps: 'firefox' }), ws({ id: 2, status: 'running' })])
    await store.fetch()
    await store.fetch()
    await vi.advanceTimersByTimeAsync(2500)
    expect(kickMock).toHaveBeenCalledTimes(1)
    // Already running: no new kick.
    await store.fetch()
    await vi.advanceTimersByTimeAsync(2500)
    expect(kickMock).toHaveBeenCalledTimes(1)
  })
})
