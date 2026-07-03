import { api } from './client'
import { useAuthStore } from '@/stores/auth'
import type { FileListing } from '@/types'

const BASE = '/api'

// zone 0 is the local control plane; any other id browses that zone's agent
// (the backend proxies the file API over mTLS). The selected zone rides along as
// a query param on every verb.
function buildQuery(path: string, zoneId = 0) {
  return `?path=${encodeURIComponent(path)}&zone_id=${zoneId}`
}

export const filesApi = {
  list: (path = '', zoneId = 0) => api.get<FileListing>('/files' + buildQuery(path, zoneId)),

  // The download endpoint accepts the session cookie (cove_session), so a plain
  // anchor href works for navigation-style downloads. We expose the URL for that
  // simple path, and also a fetch-based download that carries the Bearer token
  // for environments where the cookie isn't present.
  downloadUrl: (path: string, zoneId = 0) => BASE + '/files/download' + buildQuery(path, zoneId),

  async download(path: string, zoneId = 0) {
    const auth = useAuthStore()
    const headers: Record<string, string> = {}
    if (auth.token) headers['Authorization'] = `Bearer ${auth.token}`
    const resp = await fetch(filesApi.downloadUrl(path, zoneId), {
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

  // Multipart upload via XHR (not fetch) so we can report upload progress — a
  // large migration-scale home would otherwise sit under an indefinite spinner
  // that reads as "hung". Must NOT set Content-Type; XHR derives the multipart
  // boundary from the FormData. onProgress receives a 0..1 fraction.
  upload(dir: string, file: File, zoneId = 0, onProgress?: (fraction: number) => void) {
    const auth = useAuthStore()
    const fd = new FormData()
    fd.append('path', dir)
    fd.append('file', file)
    return new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', BASE + `/files/upload?zone_id=${zoneId}`)
      if (auth.token) xhr.setRequestHeader('Authorization', `Bearer ${auth.token}`)
      xhr.withCredentials = true
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve()
          return
        }
        let detail = `Upload failed (HTTP ${xhr.status})`
        try {
          detail = JSON.parse(xhr.responseText).detail || detail
        } catch {}
        reject(new Error(detail))
      }
      xhr.onerror = () => reject(new Error('Upload failed — network error'))
      xhr.send(fd)
    })
  },

  remove: (path: string, zoneId = 0) => api.delete('/files' + buildQuery(path, zoneId)),
}
