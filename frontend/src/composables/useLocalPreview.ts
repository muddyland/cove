import { onUnmounted, ref } from 'vue'
import { usePreviewsStore } from '@/stores/previews'

/**
 * Refresh a workspace's grid preview from the stream you're currently watching.
 *
 * **Nothing leaves the browser.** The frame is assembled here and handed to the
 * previews store for this tab only — it is never POSTed back, so the server's
 * copy stays the one frame taken at launch. That is the whole point: the grid
 * gets to look current without Cove accumulating pictures of your desktop.
 *
 * We open our own short-lived WebSocket to the stream rather than reading the
 * iframe, because in subdomain mode the iframe is cross-origin and unreachable.
 * The socket is strictly passive — it never sends `SETTINGS`, which is what
 * would make Selkies treat it as a new primary client and evict the very session
 * the user is watching. It simply picks up frames already being broadcast.
 */

const SOI = [0xff, 0xd8, 0xff]

/**
 * Locate the JPEG start-of-image inside a stripe message's header region.
 * Exported for testing — the header is undocumented and its parsing is the part
 * most likely to break silently against a future Selkies release.
 */
export function soiOffset(bytes: Uint8Array): number {
  for (let i = 2; i <= 16 && i + 2 < bytes.length; i++) {
    if (bytes[i] === SOI[0] && bytes[i + 1] === SOI[1] && bytes[i + 2] === SOI[2]) return i
  }
  return -1
}

/** Build the stream's websocket URL from the iframe URL the API minted. */
export function streamSocketUrl(streamUrl: string): string | null {
  try {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    // Subdomain mode: "//host/?__cove_t=..." — protocol-relative, own origin.
    if (streamUrl.startsWith('//')) {
      const u = new URL(location.protocol + streamUrl)
      return `${proto}//${u.host}/websockets`
    }
    // Subpath mode: "/workspace/<public_id>/" on our own origin; Traefik strips
    // the prefix before the container sees it.
    const path = streamUrl.endsWith('/') ? streamUrl : streamUrl + '/'
    return `${proto}//${location.host}${path}websockets`
  } catch {
    return null
  }
}

/** Collect one complete frame from a passive stream socket. */
async function grabFrame(url: string, timeoutMs = 6000): Promise<Blob | null> {
  if (typeof createImageBitmap !== 'function' || typeof OffscreenCanvas === 'undefined') return null

  const stripes = new Map<number, Uint8Array>()

  let ws: WebSocket
  try {
    ws = new WebSocket(url)
  } catch {
    return null // malformed URL / blocked scheme
  }
  ws.binaryType = 'arraybuffer'

  try {
    await new Promise<void>(resolve => {
      let hard: ReturnType<typeof setTimeout> | undefined
      let quiet: ReturnType<typeof setTimeout> | undefined
      const done = () => {
        if (hard) clearTimeout(hard)
        if (quiet) clearTimeout(quiet)
        resolve()
      }
      hard = setTimeout(done, timeoutMs)
      // A socket error is just "no frame this time" — resolve and let the
      // caller keep whatever preview it already had, rather than throwing.
      ws.onerror = done
      ws.onclose = done
      ws.onmessage = ev => {
        if (typeof ev.data === 'string') return
        const bytes = new Uint8Array(ev.data as ArrayBuffer)
        const off = soiOffset(bytes)
        if (off < 0) return
        const y = (bytes[off - 2] << 8) | bytes[off - 1]
        // Frames arrive as a burst; once it stops growing, we have them all.
        if (!stripes.has(y)) {
          if (quiet) clearTimeout(quiet)
          quiet = setTimeout(done, 700)
        }
        stripes.set(y, bytes.subarray(off))
      }
    })
  } finally {
    try {
      ws.close()
    } catch {
      /* already closing */
    }
  }

  if (!stripes.size) return null

  // Decode every stripe, then require them to tile the frame exactly — a partial
  // set would render with black bands through it, which looks like a broken
  // workspace rather than a missing preview.
  const ordered = [...stripes.entries()].sort((a, b) => a[0] - b[0])
  const tiles: { y: number; bmp: ImageBitmap }[] = []
  try {
    for (const [y, data] of ordered) {
      tiles.push({ y, bmp: await createImageBitmap(new Blob([data as BlobPart], { type: 'image/jpeg' })) })
    }
    let cursor = 0
    for (const t of tiles) {
      if (t.y !== cursor) return null
      cursor = t.y + t.bmp.height
    }
    const width = Math.max(...tiles.map(t => t.bmp.width))
    const canvas = new OffscreenCanvas(width, cursor)
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    for (const t of tiles) ctx.drawImage(t.bmp, 0, t.y)
    return await canvas.convertToBlob({ type: 'image/jpeg', quality: 0.72 })
  } catch {
    return null
  } finally {
    for (const t of tiles) t.bmp.close()
  }
}

/** How often to refresh while the workspace is open. */
const REFRESH_MS = 60_000

export function useLocalPreview(wsId: () => number | null, streamUrl: () => string | null) {
  const previews = usePreviewsStore()
  const active = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  async function capture() {
    const id = wsId()
    const stream = streamUrl()
    if (!id || !stream || active.value) return
    // Don't burn a socket (or bandwidth) refreshing a preview nobody can see.
    if (document.visibilityState === 'hidden') return
    const url = streamSocketUrl(stream)
    if (!url) return
    active.value = true
    try {
      const blob = await grabFrame(url)
      if (blob && wsId() === id) await previews.setLocal(id, blob)
    } catch {
      // Best-effort: a failed grab just leaves the launch frame in place.
    } finally {
      active.value = false
    }
  }

  function start() {
    stop()
    // One shot shortly after the stream settles, then on a slow cadence.
    setTimeout(capture, 4000)
    timer = setInterval(capture, REFRESH_MS)
  }

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
  }

  onUnmounted(stop)
  return { start, stop, capture }
}
