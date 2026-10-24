import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { ThinClientSelf, Workspace } from '@/types'

const self = vi.fn()
vi.mock('@/api/thinClients', () => ({ thinClientsApi: { self: () => self() } }))

import { useThinClientStore } from '@/stores/thinClient'

function ws(id: number, status: Workspace['status'], connectable = true, public_id = `p${id}`): Workspace {
  return { id, public_id, status, connectable } as unknown as Workspace
}
function device(over: Partial<ThinClientSelf> = {}): ThinClientSelf {
  return {
    public_id: 'tc', name: 'Workbench', auto_open: 'running', pinned_workspace_id: null,
    signout_minutes: 30, screen_off_minutes: 15, ...over,
  }
}

describe('thin client store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    self.mockReset()
  })

  it('stays inert on an ordinary browser', async () => {
    self.mockRejectedValue(new Error('404'))
    const store = useThinClientStore()
    const push = vi.fn()
    await store.maybeAutoOpen([ws(1, 'running')], { push } as any, vi.fn())
    expect(store.info).toBeNull()
    expect(push).not.toHaveBeenCalled()
  })

  it('opens the only running workspace, once per page load', async () => {
    self.mockResolvedValue(device())
    const store = useThinClientStore()
    const push = vi.fn()
    await store.maybeAutoOpen([ws(1, 'running'), ws(2, 'stopped')], { push } as any, vi.fn())
    expect(push).toHaveBeenCalledWith('/app/workspace/1')
    await store.maybeAutoOpen([ws(1, 'running')], { push } as any, vi.fn())
    expect(push).toHaveBeenCalledTimes(1)
  })

  it('does nothing when several are running or one is still booting', () => {
    self.mockResolvedValue(device())
    const store = useThinClientStore()
    store.info = device()
    expect(store.autoOpenTarget([ws(1, 'running'), ws(2, 'running')])).toBeNull()
    expect(store.autoOpenTarget([ws(1, 'running', false)])).toBeNull()
  })

  it('starts a halted pinned workspace before opening it', async () => {
    self.mockResolvedValue(device({ auto_open: 'pinned', pinned_workspace_id: 'kvm' }))
    const store = useThinClientStore()
    const push = vi.fn()
    const start = vi.fn().mockResolvedValue(undefined)
    await store.maybeAutoOpen([ws(1, 'running'), ws(7, 'stopped', false, 'kvm')], { push } as any, start)
    expect(start).toHaveBeenCalledWith(7)
    expect(push).toHaveBeenCalledWith('/app/workspace/7')
  })

  it('ignores a pinned workspace the signed-in user does not own', async () => {
    self.mockResolvedValue(device({ auto_open: 'pinned', pinned_workspace_id: 'someone-elses' }))
    const store = useThinClientStore()
    const push = vi.fn()
    await store.maybeAutoOpen([ws(1, 'running')], { push } as any, vi.fn())
    expect(push).not.toHaveBeenCalled()
  })
})
