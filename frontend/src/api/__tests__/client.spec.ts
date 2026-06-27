import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { api, ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

// Build a minimal Response-like object for our fetch mock.
function jsonResponse(status: number, body: unknown = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

// Capture window.location writes without triggering a real navigation (jsdom
// throws "Not implemented: navigation" on href assignment).
let locationHref: string

describe('api client', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())

    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    locationHref = '/'
    // Replace window.location with a settable href stub.
    vi.stubGlobal('location', {
      get href() {
        return locationHref
      },
      set href(v: string) {
        locationHref = v
      },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('GET sends credentials:include and no Authorization header without a token', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { hello: 'world' }))

    const result = await api.get<{ hello: string }>('/things')

    expect(result).toEqual({ hello: 'world' })
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/things')
    expect(init.credentials).toBe('include')
    expect(init.method).toBe('GET')
    expect(init.headers).not.toHaveProperty('Authorization')
  })

  it('GET sends the Authorization Bearer header when a token exists', async () => {
    const auth = useAuthStore()
    auth.setToken('my-token')
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}))

    await api.get('/secure')

    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers.Authorization).toBe('Bearer my-token')
    expect(init.credentials).toBe('include')
  })

  it('on 401, refreshes once and retries the original request with the new token', async () => {
    const auth = useAuthStore()
    auth.setToken('old-token')

    fetchMock
      // 1. original request -> 401
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      // 2. refresh -> new token
      .mockResolvedValueOnce(jsonResponse(200, { access_token: 'new-token' }))
      // 3. retried original request -> success
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }))

    const result = await api.get<{ ok: boolean }>('/data')

    expect(result).toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(3)

    // Call 2 is the refresh endpoint.
    expect(fetchMock.mock.calls[1][0]).toBe('/api/auth/refresh')
    expect(fetchMock.mock.calls[1][1].method).toBe('POST')

    // Call 3 is the retry with the refreshed bearer token.
    expect(fetchMock.mock.calls[2][0]).toBe('/api/data')
    expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe('Bearer new-token')

    // Store now holds the new token.
    expect(auth.token).toBe('new-token')
    expect(locationHref).toBe('/')
  })

  it('clears auth and redirects to /app/login (no loop) when refresh fails', async () => {
    const auth = useAuthStore()
    auth.setToken('old-token')

    fetchMock
      // 1. original -> 401
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      // 2. refresh -> failure (no token)
      .mockResolvedValueOnce(jsonResponse(401, {}))

    await expect(api.get('/data')).rejects.toBeInstanceOf(ApiError)

    // Exactly the original + one refresh attempt — no looping retry.
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1][0]).toBe('/api/auth/refresh')

    expect(auth.token).toBeNull()
    expect(localStorage.getItem('cove_token')).toBeNull()
    expect(locationHref).toBe('/app/login')
  })

  it('clears auth and redirects when refresh succeeds but the retry still 401s', async () => {
    const auth = useAuthStore()
    auth.setToken('old-token')

    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, {})) // original
      .mockResolvedValueOnce(jsonResponse(200, { access_token: 'new-token' })) // refresh
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'still bad' })) // retry

    await expect(api.get('/data')).rejects.toBeInstanceOf(ApiError)

    // original + refresh + single retry = 3, no further refresh.
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(auth.token).toBeNull()
    expect(locationHref).toBe('/app/login')
  })

  it('does NOT refresh on a 401 when no token existed (e.g. bad login)', async () => {
    // No token set — a 401 here is a credential failure, not an expired session.
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: 'bad creds' }))

    await expect(api.post('/auth/login', { username: 'x', password: 'y' })).rejects.toMatchObject({
      status: 401,
      message: 'bad creds',
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(locationHref).toBe('/')
  })

  it('does NOT recurse when the refresh endpoint itself returns 401', async () => {
    const auth = useAuthStore()
    auth.setToken('tok')
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: 'no refresh' }))

    // Call the refresh path directly through the client.
    await expect(api.post('/auth/refresh')).rejects.toBeInstanceOf(ApiError)

    // Only one call — the refresh path is excluded from the refresh cycle.
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/auth/refresh')
  })

  it('throws ApiError with detail on a non-401 error', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(500, { detail: 'boom' }))
    await expect(api.get('/data')).rejects.toMatchObject({ status: 500, message: 'boom' })
  })

  it('returns undefined for a 204 No Content response', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(204))
    const result = await api.delete('/thing/1')
    expect(result).toBeUndefined()
  })

  it('POST serializes the body as JSON with a content-type header', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}))
    await api.post('/things', { a: 1 })
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ a: 1 }))
    expect(init.headers['Content-Type']).toBe('application/json')
  })
})

describe('api client error formatting', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('renders a FastAPI 422 array detail as a readable string (not [object Object])', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, { detail: [{ type: 'missing', loc: ['body', 'x'], msg: 'Field required' }] }),
    )
    await expect(api.post('/things', {})).rejects.toMatchObject({
      status: 422,
      message: 'Field required',
    })
  })

  it('passes a string detail through unchanged', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(409, { detail: 'Already on that zone' }))
    await expect(api.post('/things', {})).rejects.toMatchObject({
      status: 409,
      message: 'Already on that zone',
    })
  })
})
