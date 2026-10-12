import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { workspaceFilesApi } from '@/api/workspaceFiles'

// Uploads into a running workspace, shared by the file browser dialog and the
// drop-onto-the-stream overlay so both show the same progress. Two run at a
// time; the rest wait their turn.
export interface WsTransfer {
  id: number
  wsId: number
  name: string
  dir: string
  /** Where it was started: the Files dialog or a drop on the stream. */
  source: 'browser' | 'stream'
  loaded: number
  total: number
  status: 'queued' | 'uploading' | 'done' | 'error'
  error?: string
  savedAs?: string
}

const CONCURRENCY = 2
let seq = 0

export const useWsTransfersStore = defineStore('wsTransfers', () => {
  const items = ref<WsTransfer[]>([])
  const listeners = new Set<(t: WsTransfer) => void>()

  const active = computed(() => items.value.filter(t => t.status === 'queued' || t.status === 'uploading'))
  const files = new Map<number, File>()

  function enqueue(wsId: number, dir: string, list: File[], source: WsTransfer['source'] = 'browser') {
    for (const f of list) {
      const t: WsTransfer = { id: ++seq, wsId, name: f.name, dir, source, loaded: 0, total: f.size, status: 'queued' }
      files.set(t.id, f)
      items.value.push(t)
    }
    pump()
  }

  function pump() {
    const running = items.value.filter(t => t.status === 'uploading').length
    const next = items.value.filter(t => t.status === 'queued').slice(0, CONCURRENCY - running)
    for (const t of next) run(t.id)
  }

  async function run(id: number) {
    const t = items.value.find(x => x.id === id)!
    const file = files.get(id)!
    t.status = 'uploading'
    try {
      const res = await workspaceFilesApi.upload(t.wsId, t.dir, file, (loaded, total) => {
        t.loaded = loaded
        t.total = total
      })
      t.status = 'done'
      t.loaded = t.total
      t.savedAs = res.name
    } catch (e: any) {
      t.status = 'error'
      t.error = e?.message || 'Upload failed'
    } finally {
      files.delete(id)
      for (const l of listeners) l(t)
      pump()
    }
  }

  /** Drop finished rows (keeps failures until dismissed explicitly). */
  function clearDone(wsId?: number, source?: WsTransfer['source']) {
    items.value = items.value.filter(
      t => t.status !== 'done' || (wsId !== undefined && t.wsId !== wsId) || (source !== undefined && t.source !== source),
    )
  }
  function dismiss(id: number) {
    items.value = items.value.filter(t => t.id !== id)
  }
  function onFinished(fn: (t: WsTransfer) => void) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  }

  return { items, active, enqueue, clearDone, dismiss, onFinished }
})
