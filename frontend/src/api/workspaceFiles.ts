import { api } from './client'
import { filesApi } from './files'
import { useAuthStore } from '@/stores/auth'
import type { WsListing, WsUpload } from '@/types'

// Files inside a running workspace, reached through the container itself
// (works for ephemeral and remote-zone workspaces too). Paths are relative to
// the workspace home.
const base = (wsId: number) => `/workspaces/${wsId}/fs`

export const workspaceFilesApi = {
  list: (wsId: number, path = '') =>
    api.get<WsListing>(`${base(wsId)}?path=${encodeURIComponent(path)}`),

  mkdir: (wsId: number, path: string) => api.post<{ path: string }>(`${base(wsId)}/mkdir`, { path }),

  download: (wsId: number, path: string) =>
    filesApi._saveAs(`/api${base(wsId)}/download?path=${encodeURIComponent(path)}`, path.split('/').pop() || 'download'),

  // XHR for upload progress (fetch can't report it). Don't set Content-Type:
  // the browser adds the multipart boundary.
  upload(wsId: number, dir: string, file: File, onProgress?: (loaded: number, total: number) => void) {
    const auth = useAuthStore()
    const fd = new FormData()
    fd.append('path', dir)
    fd.append('file', file)
    return new Promise<WsUpload>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `/api${base(wsId)}/upload`)
      if (auth.token) xhr.setRequestHeader('Authorization', `Bearer ${auth.token}`)
      xhr.withCredentials = true
      xhr.upload.onprogress = e => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total)
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText))
          } catch {
            resolve({ path: dir, name: file.name, size: file.size })
          }
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
}
