import { api } from './client'
import { useAuthStore } from '@/stores/auth'
import type { FileListing, TrashEntry } from '@/types'

const BASE = '/api'

// A file selected via a folder <input webkitdirectory> / drag-dropped folder
// carries a relativePath ("sub/dir/file.txt"); loose files have "".
export interface UploadItem {
  file: File
  relativePath: string
}

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

  // Fetch a URL with auth and save the response as a file. Shared by single-file
  // and archive (folder) downloads.
  async _saveAs(url: string, filename: string) {
    const auth = useAuthStore()
    const headers: Record<string, string> = {}
    if (auth.token) headers['Authorization'] = `Bearer ${auth.token}`
    const resp = await fetch(url, { headers, credentials: 'include' })
    if (!resp.ok) throw new Error(`Download failed (HTTP ${resp.status})`)
    const blob = await resp.blob()
    const objUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = objUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(objUrl)
  },

  download(path: string, zoneId = 0) {
    return filesApi._saveAs(filesApi.downloadUrl(path, zoneId), path.split('/').pop() || 'download')
  },

  // Download a folder (or file) as a zip streamed by the server.
  //
  // Fetches over the network (a plain same-origin request the service worker
  // passes straight through — an <a download> navigation to /api was being
  // swallowed by the SW, so no request fired at all). When the File System
  // Access API is available (Chromium) the response body is streamed directly to
  // the chosen file with NO memory ceiling, so multi-GB folders work; elsewhere
  // it falls back to a blob (fine for moderate sizes). The save-picker must be
  // opened first, inside the click gesture, before we await the network.
  async downloadArchive(path: string, zoneId = 0) {
    const url = BASE + '/files/download-archive' + buildQuery(path, zoneId)
    const filename = (path.split('/').pop() || 'archive') + '.zip'
    const picker = (window as unknown as { showSaveFilePicker?: (o: unknown) => Promise<any> }).showSaveFilePicker

    // 1) Open the OS save dialog first (streaming path), while still in the gesture.
    let writable: { write: (d: Uint8Array) => Promise<void>; close: () => Promise<void>; abort: () => Promise<void> } | null = null
    if (picker) {
      try {
        const handle = await picker({
          suggestedName: filename,
          types: [{ description: 'Zip archive', accept: { 'application/zip': ['.zip'] } }],
        })
        writable = await handle.createWritable()
      } catch (e: any) {
        if (e?.name === 'AbortError') return // user cancelled the save dialog
        writable = null // any other picker error → fall back to a blob download
      }
    }

    // 2) Fetch (Bearer + cookie both accepted by the endpoint).
    const auth = useAuthStore()
    const headers: Record<string, string> = {}
    if (auth.token) headers['Authorization'] = `Bearer ${auth.token}`
    const resp = await fetch(url, { headers, credentials: 'include' })
    if (!resp.ok) {
      if (writable) await writable.abort().catch(() => {})
      throw new Error(`Download failed (HTTP ${resp.status})`)
    }

    // 3a) Stream to disk.
    if (writable && resp.body) {
      await resp.body.pipeTo(writable as unknown as WritableStream)
      return
    }
    // 3b) Fallback: buffer as a blob and click a blob: URL (never a navigation).
    const blob = await resp.blob()
    const objUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = objUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(objUrl)
  },

  copy: (src: string, dstDir: string, zoneId = 0) =>
    api.post('/files/copy?zone_id=' + zoneId, { src, dst_dir: dstDir }),

  move: (src: string, dstDir: string, zoneId = 0) =>
    api.post('/files/move?zone_id=' + zoneId, { src, dst_dir: dstDir }),

  // ── Trash ──
  trash: (path: string, zoneId = 0) =>
    api.post<TrashEntry>('/files/trash?zone_id=' + zoneId, { path }),
  listTrash: (zoneId = 0) => api.get<TrashEntry[]>('/files/trash?zone_id=' + zoneId),
  restore: (id: number) => api.post(`/files/trash/${id}/restore`),
  purge: (id: number) => api.delete(`/files/trash/${id}`),

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
