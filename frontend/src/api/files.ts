import { api } from './client'
import { useAuthStore } from '@/stores/auth'
import type { FileListing } from '@/types'

const BASE = '/api'

function buildQuery(path: string) {
  return `?path=${encodeURIComponent(path)}`
}

export const filesApi = {
  list: (path = '') => api.get<FileListing>('/files' + buildQuery(path)),

  // The download endpoint accepts the session cookie (cove_session), so a plain
  // anchor href works for navigation-style downloads. We expose the URL for that
  // simple path, and also a fetch-based download that carries the Bearer token
  // for environments where the cookie isn't present.
  downloadUrl: (path: string) => BASE + '/files/download' + buildQuery(path),

  async download(path: string) {
    const auth = useAuthStore()
    const headers: Record<string, string> = {}
    if (auth.token) headers['Authorization'] = `Bearer ${auth.token}`
    const resp = await fetch(filesApi.downloadUrl(path), {
      headers,
      credentials: 'include',
    })
    if (!resp.ok) throw new Error(`Download failed (HTTP ${resp.status})`)
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = path.split('/').pop() || 'download'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  // Multipart upload: must NOT set Content-Type so the browser sets the
  // multipart boundary. Carry the Bearer header from the auth store plus the
  // session cookie via credentials:'include'.
  async upload(dir: string, file: File) {
    const auth = useAuthStore()
    const headers: Record<string, string> = {}
    if (auth.token) headers['Authorization'] = `Bearer ${auth.token}`
    const fd = new FormData()
    fd.append('path', dir)
    fd.append('file', file)
    const resp = await fetch(BASE + '/files/upload', {
      method: 'POST',
      headers,
      credentials: 'include',
      body: fd,
    })
    if (!resp.ok) {
      let detail = `Upload failed (HTTP ${resp.status})`
      try {
        const body = await resp.json()
        detail = body.detail || detail
      } catch {}
      throw new Error(detail)
    }
  },

  remove: (path: string) => api.delete('/files' + buildQuery(path)),
}
