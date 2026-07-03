import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { filesApi } from '@/api/files'
import { useAuthStore } from '@/stores/auth'

function jsonResponse(status: number, body: unknown = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    blob: async () => new Blob(['data']),
  } as unknown as Response
}

describe('filesApi', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('list() requests /files with an encoded path query (default zone 0)', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { path: 'a/b', entries: [] }))
    const res = await filesApi.list('a/b')
    expect(res).toEqual({ path: 'a/b', entries: [] })
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/files?path=a%2Fb&zone_id=0')
  })

  it('list() targets a remote zone when given a zone id', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { path: '', entries: [] }))
    await filesApi.list('', 3)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/files?path=&zone_id=3')
  })

  it('downloadUrl() builds the download endpoint URL', () => {
    expect(filesApi.downloadUrl('docs/file.txt')).toBe(
      '/api/files/download?path=docs%2Ffile.txt&zone_id=0',
    )
  })

  it('upload() posts multipart via XHR with the Bearer header + progress', async () => {
    const auth = useAuthStore()
    auth.setToken('tok-1')

    const setRequestHeader = vi.fn()
    let method = ''
    let url = ''
    let body: unknown = null
    const fakeXhr: Record<string, any> = {
      open: (m: string, u: string) => { method = m; url = u },
      setRequestHeader,
      upload: {},
      withCredentials: false,
      send(b: unknown) {
        body = b
        fakeXhr.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 })
        fakeXhr.status = 200
        fakeXhr.responseText = '{}'
        fakeXhr.onload()
      },
    }
    vi.stubGlobal('XMLHttpRequest', vi.fn(() => fakeXhr))

    const file = new File(['hi'], 'hi.txt', { type: 'text/plain' })
    const progress: number[] = []
    await filesApi.upload('sub', file, 0, f => progress.push(f))

    expect(method).toBe('POST')
    expect(url).toBe('/api/files/upload?zone_id=0')
    expect(fakeXhr.withCredentials).toBe(true)
    expect(setRequestHeader).toHaveBeenCalledWith('Authorization', 'Bearer tok-1')
    expect(body).toBeInstanceOf(FormData)
    expect((body as FormData).get('path')).toBe('sub')
    expect(progress).toEqual([0.5])
  })

  it('remove() issues a DELETE against /files with the path query', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(204))
    await filesApi.remove('x/y.txt')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/files?path=x%2Fy.txt&zone_id=0')
    expect(init.method).toBe('DELETE')
  })

  it('download() fetches the blob and triggers an anchor click', async () => {
    const auth = useAuthStore()
    auth.setToken('tok-2')
    fetchMock.mockResolvedValueOnce(jsonResponse(200))

    // Stub object URL helpers (jsdom lacks them).
    const createObjectURL = vi.fn(() => 'blob:mock')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await filesApi.download('dir/file.txt')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/files/download?path=dir%2Ffile.txt&zone_id=0')
    expect(init.headers.Authorization).toBe('Bearer tok-2')
    expect(createObjectURL).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalled()
  })
})
