import { useAuthStore } from '@/stores/auth'

const BASE = '/api'

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

// Attempt a single token refresh via the raw fetch (never recurses through
// request(), so it can't loop). Returns the new access token, or null on failure.
async function tryRefresh(): Promise<string | null> {
  try {
    const resp = await fetch(BASE + '/auth/refresh', {
      method: 'POST',
      credentials: 'include',
    })
    if (!resp.ok) return null
    const body = await resp.json()
    return body?.access_token ?? null
  } catch {
    return null
  }
}

async function request<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const auth = useAuthStore()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string>),
  }
  if (auth.token) {
    headers['Authorization'] = `Bearer ${auth.token}`
  }

  const resp = await fetch(BASE + path, { ...init, headers, credentials: 'include' })

  if (resp.status === 401) {
    const hadToken = !!auth.token
    // Try a single refresh-then-retry cycle. Never retry more than once, and
    // never trigger this for the refresh endpoint itself. Only attempt refresh
    // when a token existed (an expired session) — login/setup 401s (no token,
    // e.g. bad credentials) should just surface their error.
    if (!retried && hadToken && path !== '/auth/refresh') {
      const newToken = await tryRefresh()
      if (newToken) {
        auth.setToken(newToken)
        // Replay the original request once with the fresh bearer header.
        return request<T>(path, init, true)
      }
      // Refresh failed for an expired session — clear and bounce to login.
      auth.clear()
      window.location.href = '/login'
    } else if (hadToken && path !== '/auth/refresh') {
      // 401 again after a successful refresh+retry — session is unusable.
      auth.clear()
      window.location.href = '/login'
    }
    let detail = 'Unauthorized'
    try {
      const body = await resp.json()
      detail = body.detail || detail
    } catch {}
    throw new ApiError(401, detail)
  }

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      detail = body.detail || detail
    } catch {}
    throw new ApiError(resp.status, detail)
  }

  if (resp.status === 204) return undefined as T
  return resp.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: 'GET' }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T = void>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export { ApiError }
