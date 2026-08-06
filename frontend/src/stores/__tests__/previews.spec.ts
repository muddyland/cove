import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usePreviewsStore } from '@/stores/previews'
import { workspacesApi } from '@/api/workspaces'
import type { Workspace } from '@/types'

vi.mock('@/api/workspaces', () => ({
  workspacesApi: { preview: vi.fn() },
}))

function ws(overrides: Partial<Workspace> = {}): Workspace {
  return { id: 1, status: 'running', preview_at: '2026-01-01T00:00:00Z', ...overrides } as Workspace
}

describe('previews store', () => {
  let created: string[]
  let revoked: string[]

  beforeEach(() => {
    setActivePinia(createPinia())
    created = []
    revoked = []
    let n = 0
    globalThis.URL.createObjectURL = vi.fn(() => {
      const url = `blob:frame-${++n}`
      created.push(url)
      return url
    })
    globalThis.URL.revokeObjectURL = vi.fn((url: string) => {
      revoked.push(url)
    })
    vi.mocked(workspacesApi.preview).mockReset()
    vi.mocked(workspacesApi.preview).mockResolvedValue(new Blob(['x']))
  })

  it('fetches a frame and exposes it by workspace id', async () => {
    const store = usePreviewsStore()
    await store.load(ws())
    expect(workspacesApi.preview).toHaveBeenCalledWith(1)
    expect(store.urls[1]).toBe('blob:frame-1')
  })

  it('does not refetch the same frame', async () => {
    const store = usePreviewsStore()
    await store.load(ws())
    await store.load(ws())
    expect(workspacesApi.preview).toHaveBeenCalledTimes(1)
  })

  it('refetches when a newer capture exists', async () => {
    const store = usePreviewsStore()
    await store.load(ws())
    await store.load(ws({ preview_at: '2026-01-02T00:00:00Z' }))
    expect(workspacesApi.preview).toHaveBeenCalledTimes(2)
    // The superseded object URL must be released, not leaked.
    expect(revoked).toContain('blob:frame-1')
    expect(store.urls[1]).toBe('blob:frame-2')
  })

  it('skips workspaces that have no capture', async () => {
    const store = usePreviewsStore()
    await store.load(ws({ preview_at: null }))
    expect(workspacesApi.preview).not.toHaveBeenCalled()
    expect(store.urls[1]).toBeUndefined()
  })

  it('falls back to the logo silently when there is no capture (404)', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(workspacesApi.preview).mockRejectedValue(
      Object.assign(new Error('No preview available'), { status: 404 }),
    )
    const store = usePreviewsStore()
    await store.load(ws())
    expect(store.urls[1]).toBeUndefined()
    // A workspace with no frame is ordinary, not a fault — don't cry wolf.
    expect(warn).not.toHaveBeenCalled()
    warn.mockRestore()
  })

  it('warns on a real failure rather than swallowing it', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(workspacesApi.preview).mockRejectedValue(
      Object.assign(new Error('Internal Server Error'), { status: 500 }),
    )
    const store = usePreviewsStore()
    await store.load(ws())
    expect(store.urls[1]).toBeUndefined()
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('keeps the existing frame when the server says 304', async () => {
    // getBlob resolves null on a revalidation. Treating that as a new frame
    // would replace a good preview with an empty blob and blank the card.
    const store = usePreviewsStore()
    await store.load(ws())
    expect(store.urls[1]).toBe('blob:frame-1')

    vi.mocked(workspacesApi.preview).mockResolvedValueOnce(null)
    await store.load(ws({ preview_at: '2026-01-02T00:00:00Z' }))
    expect(store.urls[1]).toBe('blob:frame-1')
    expect(revoked).not.toContain('blob:frame-1')
  })

  it('retries later if a 304 arrives with nothing already cached', async () => {
    const store = usePreviewsStore()
    vi.mocked(workspacesApi.preview).mockResolvedValueOnce(null)
    await store.load(ws())
    expect(store.urls[1]).toBeUndefined()
    // The marker must not have been recorded, or the frame would never load.
    await store.load(ws())
    expect(workspacesApi.preview).toHaveBeenCalledTimes(2)
  })

  it('setLocal replaces the frame without any upload', async () => {
    const store = usePreviewsStore()
    await store.load(ws())
    store.setLocal(1, new Blob(['local']))
    expect(store.urls[1]).toBe('blob:frame-2')
    expect(revoked).toContain('blob:frame-1')
    // Only the initial load ever talked to the server.
    expect(workspacesApi.preview).toHaveBeenCalledTimes(1)
  })

  it('a locally-set frame can still be superseded by a newer server frame', async () => {
    const store = usePreviewsStore()
    await store.load(ws())
    store.setLocal(1, new Blob(['local']))
    await store.load(ws())
    expect(workspacesApi.preview).toHaveBeenCalledTimes(2)
  })

  it('clear drops the frame and releases its url', async () => {
    const store = usePreviewsStore()
    await store.load(ws())
    store.clear(1)
    expect(store.urls[1]).toBeUndefined()
    expect(revoked).toContain('blob:frame-1')
    // Cleared, so a later load must fetch again rather than assume it is current.
    await store.load(ws())
    expect(workspacesApi.preview).toHaveBeenCalledTimes(2)
  })

  it('clearAll releases every frame (logout / session expiry)', async () => {
    const store = usePreviewsStore()
    await store.load(ws({ id: 1 }))
    await store.load(ws({ id: 2 }))
    store.clearAll()
    expect(store.urls).toEqual({})
    expect(revoked).toHaveLength(created.length)
  })
})
