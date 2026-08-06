import { defineStore } from 'pinia'
import { ref } from 'vue'
import { workspacesApi } from '@/api/workspaces'
import type { Workspace } from '@/types'

/**
 * Screen previews for the workspace grid.
 *
 * Deliberately **in-memory only** — object URLs in a ref, nothing in
 * localStorage/sessionStorage/IndexedDB. These are pictures of the user's
 * desktop, so they live for the lifetime of the page and no longer; closing the
 * tab leaves nothing behind on disk.
 *
 * Two sources feed the same map:
 *  - the server-side capture taken at launch, fetched on demand (`load`)
 *  - frames grabbed locally from a stream the user is watching (`setLocal`)
 *
 * Locally-grabbed frames are **never uploaded**. They refresh what this browser
 * shows and go no further, which is why a reload falls back to the launch frame
 * rather than whatever the workspace looked like a moment ago.
 */
export const usePreviewsStore = defineStore('previews', () => {
  // workspace id -> object URL of the frame currently displayed
  const urls = ref<Record<number, string>>({})
  // workspace id -> the preview_at we fetched, so we refetch only on a new frame
  const fetchedAt = ref<Record<number, string>>({})
  const inFlight = new Set<number>()

  function revoke(id: number) {
    const existing = urls.value[id]
    if (existing) URL.revokeObjectURL(existing)
  }

  function put(id: number, url: string) {
    revoke(id)
    urls.value[id] = url
  }

  /** Fetch the server-side capture, unless we already hold that exact frame. */
  async function load(ws: Workspace) {
    if (!ws.preview_at) return
    if (fetchedAt.value[ws.id] === ws.preview_at) return
    if (inFlight.has(ws.id)) return
    inFlight.add(ws.id)
    try {
      const blob = await workspacesApi.preview(ws.id)
      put(ws.id, URL.createObjectURL(blob))
      fetchedAt.value[ws.id] = ws.preview_at
    } catch {
      // 404 is the normal "no capture for this workspace" case (stopped, still
      // booting, or an image whose stream we can't read) — the card just shows
      // its logo. Nothing here is worth interrupting the user over.
    } finally {
      inFlight.delete(ws.id)
    }
  }

  /**
   * Replace a workspace's preview with a frame grabbed from the live stream in
   * this browser. Local only — never sent to the server.
   */
  function setLocal(id: number, blob: Blob) {
    put(id, URL.createObjectURL(blob))
    // Clear the fetch marker so a genuinely newer server frame can still win.
    delete fetchedAt.value[id]
  }

  /** Drop a workspace's preview (halted, purged, or errored). */
  function clear(id: number) {
    revoke(id)
    delete urls.value[id]
    delete fetchedAt.value[id]
  }

  function clearAll() {
    for (const id of Object.keys(urls.value)) revoke(Number(id))
    urls.value = {}
    fetchedAt.value = {}
  }

  return { urls, load, setLocal, clear, clearAll }
})
