import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { Workspace } from '@/types'

vi.mock('@/api/workspaces', () => ({
  workspacesApi: { list: vi.fn(), stats: vi.fn() },
}))

import { workspacesApi } from '@/api/workspaces'
import { useWorkspacesStore } from '@/stores/workspaces'

const ws = (over: Partial<Workspace>) => ({ id: 1, status: 'running', connectable: true, ...over }) as Workspace

describe('workspaces store polling', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    vi.mocked(workspacesApi.list).mockReset()
    vi.mocked(workspacesApi.stats).mockReset().mockResolvedValue({})
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps polling a running workspace until it is connectable, then stops', async () => {
    vi.mocked(workspacesApi.list)
      .mockResolvedValueOnce([ws({ connectable: false })])
      .mockResolvedValueOnce([ws({ connectable: false })])
      .mockResolvedValue([ws({ connectable: true })])
    const store = useWorkspacesStore()
    await store.fetch()
    expect(workspacesApi.list).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(3000)
    expect(store.items[0].connectable).toBe(false)
    await vi.advanceTimersByTimeAsync(3000)
    expect(store.items[0].connectable).toBe(true)

    const calls = vi.mocked(workspacesApi.list).mock.calls.length
    await vi.advanceTimersByTimeAsync(30000)
    expect(workspacesApi.list).toHaveBeenCalledTimes(calls)
    store.stopPolling()
  })

  it('does not poll when every running workspace is already connectable', async () => {
    vi.mocked(workspacesApi.list).mockResolvedValue([ws({})])
    const store = useWorkspacesStore()
    await store.fetch()
    await vi.advanceTimersByTimeAsync(30000)
    expect(workspacesApi.list).toHaveBeenCalledTimes(1)
    store.stopPolling()
  })
})
