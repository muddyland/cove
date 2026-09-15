import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { ProotTask, WorkspaceProotTasks } from '@/types'

vi.mock('@/api/proot', () => ({
  prootApi: { allTasks: vi.fn() },
}))

const toastMock = vi.fn()
vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({ toast: toastMock }),
}))

import { prootApi } from '@/api/proot'
import { useTasksStore } from '@/stores/tasks'

function task(overrides: Partial<ProotTask> = {}): ProotTask {
  return {
    id: '000000000001-ab', op: 'update', state: 'running', exit_code: null, apps: ['firefox'],
    failed_apps: [], current_app: 'firefox', done_count: 0, created_at: 1, started_at: 2, finished_at: null,
    ...overrides,
  }
}

function group(tasks: ProotTask[]): WorkspaceProotTasks[] {
  return [{ workspace_id: 7, workspace_name: 'desk', tasks }]
}

describe('tasks store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    toastMock.mockReset()
    vi.mocked(prootApi.allTasks).mockReset()
    vi.mocked(prootApi.allTasks).mockResolvedValue([])
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('counts only queued and running tasks as active', async () => {
    vi.mocked(prootApi.allTasks).mockResolvedValue(
      group([task(), task({ id: 'b', state: 'queued' }), task({ id: 'c', state: 'done' })]),
    )
    const store = useTasksStore()
    await store.refresh()
    expect(store.activeCount).toBe(2)
    expect(store.total).toBe(3)
  })

  it('toasts and bumps finishedTick when a task it saw running finishes', async () => {
    const store = useTasksStore()
    vi.mocked(prootApi.allTasks).mockResolvedValueOnce(group([task()]))
    await store.refresh()
    expect(toastMock).not.toHaveBeenCalled()

    vi.mocked(prootApi.allTasks).mockResolvedValueOnce(group([task({ state: 'failed', failed_apps: ['firefox'] })]))
    await store.refresh()
    expect(toastMock).toHaveBeenCalledWith(expect.stringContaining('desk'), 'error')
    expect(store.finishedTick).toBe(1)

    // Already reported: no second toast.
    vi.mocked(prootApi.allTasks).mockResolvedValueOnce(group([task({ state: 'failed' })]))
    await store.refresh()
    expect(toastMock).toHaveBeenCalledTimes(1)
  })

  it('does not toast for tasks that were already finished when first seen', async () => {
    vi.mocked(prootApi.allTasks).mockResolvedValue(group([task({ state: 'done' })]))
    const store = useTasksStore()
    await store.refresh()
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('never overlaps requests', async () => {
    let resolve!: (v: WorkspaceProotTasks[]) => void
    vi.mocked(prootApi.allTasks).mockImplementationOnce(() => new Promise(r => { resolve = r }))
    const store = useTasksStore()
    const first = store.refresh()
    await store.refresh()
    expect(prootApi.allTasks).toHaveBeenCalledTimes(1)
    resolve([])
    await first
  })

  it('polls fast while active, slowly when idle, and stops with the last subscriber', async () => {
    vi.mocked(prootApi.allTasks).mockResolvedValue(group([task()]))
    const store = useTasksStore()
    const stopA = store.subscribe()
    const stopB = store.subscribe()
    await vi.advanceTimersByTimeAsync(0)
    expect(prootApi.allTasks).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(4000)
    expect(prootApi.allTasks).toHaveBeenCalledTimes(2)

    vi.mocked(prootApi.allTasks).mockResolvedValue([])
    await vi.advanceTimersByTimeAsync(4000)
    expect(prootApi.allTasks).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(10000)
    expect(prootApi.allTasks).toHaveBeenCalledTimes(3) // idle: 30s

    stopA()
    stopA() // idempotent
    await vi.advanceTimersByTimeAsync(30000)
    expect(prootApi.allTasks).toHaveBeenCalledTimes(4) // B still subscribed

    stopB()
    await vi.advanceTimersByTimeAsync(120000)
    expect(prootApi.allTasks).toHaveBeenCalledTimes(4)
  })

  it('keeps the last list through a failed refresh', async () => {
    const store = useTasksStore()
    vi.mocked(prootApi.allTasks).mockResolvedValueOnce(group([task()]))
    await store.refresh()
    vi.mocked(prootApi.allTasks).mockRejectedValueOnce(new Error('down'))
    await store.refresh()
    expect(store.groups).toHaveLength(1)
  })
})
