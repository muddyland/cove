// One vocabulary for workspace state, shared by the grid, the stream page, the
// quick-switch menu and the admin views. Before this the same state was called
// Online/Running, Halting/Stopping, Booting/Starting and Purge/Terminate/Delete
// depending on where you looked.
import type { Workspace, WorkspaceStatus } from '@/types'

export const STATUS_LABEL: Record<WorkspaceStatus, string> = {
  creating: 'Starting',
  running: 'Running',
  stopping: 'Stopping',
  migrating: 'Migrating',
  stopped: 'Stopped',
  error: 'Error',
}

/** Status as the user should see it: a running workspace whose stream hasn't
 *  rendered its first frame can't be connected to yet, so it is still Starting. */
export function displayStatus(ws: Pick<Workspace, 'status' | 'connectable'>): WorkspaceStatus {
  return ws.status === 'running' && !ws.connectable ? 'creating' : ws.status
}

export function statusLabel(status: WorkspaceStatus | string): string {
  return STATUS_LABEL[status as WorkspaceStatus] ?? status
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !isFinite(n)) return '—'
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB'
  if (n >= 1024 ** 2) return Math.round(n / 1024 ** 2) + ' MB'
  if (n >= 1024) return Math.round(n / 1024) + ' KB'
  return n + ' B'
}

/** The API's naive-UTC timestamps (no zone suffix) parsed as UTC. */
export function parseTs(ts: string | null | undefined): number | null {
  if (!ts) return null
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? ts : ts + 'Z'
  const t = Date.parse(iso)
  return isNaN(t) ? null : t
}

/** "45s", "12 min", "3 h 5 min", "4 d" — compact, for uptime and countdowns. */
export function fmtDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`
  const d = Math.floor(h / 24)
  return `${d} d`
}

/** "just now", "5 min ago", "3 d ago". */
export function relativeTime(ts: string | number | null | undefined, now = Date.now()): string {
  const t = typeof ts === 'number' ? ts : parseTs(ts ?? null)
  if (t === null) return '—'
  const diff = now - t
  if (diff < 45_000) return 'just now'
  return `${fmtDuration(diff)} ago`
}

/** Absolute, locale-formatted timestamp for tooltips. */
export function absoluteTime(ts: string | null | undefined): string {
  const t = parseTs(ts)
  return t === null ? '' : new Date(t).toLocaleString()
}

/** When a running workspace will be auto-stopped by the admin max-runtime
 *  limit (ms epoch), or null when there is no limit / it isn't running. */
export function autoStopAt(
  ws: Pick<Workspace, 'status' | 'started_at' | 'runtime_extended_at'>,
  maxHours: number | null,
): number | null {
  if (ws.status !== 'running' || !maxHours) return null
  const anchors = [parseTs(ws.started_at), parseTs(ws.runtime_extended_at)].filter(
    (t): t is number => t !== null,
  )
  if (!anchors.length) return null
  return Math.max(...anchors) + maxHours * 3_600_000
}

/** The extra detail shown under a workspace that's still coming up. */
export function startingDetail(ws: Workspace): string | null {
  if (ws.status === 'creating') {
    const p = ws.pull_progress
    if (p) {
      return p.total > 0
        ? `Downloading image · ${fmtBytes(p.current)} / ${fmtBytes(p.total)}`
        : 'Checking for image updates…'
    }
    if (!ws.container_id) return 'Preparing…'
    if (ws.install_packages || ws.proot_apps || ws.appimages) return 'Installing packages & apps…'
    return 'Starting container…'
  }
  if (ws.status === 'running' && !ws.connectable) {
    return ws.workspace_type === 'desktop' ? 'Waiting for the desktop to draw…' : 'Waiting for the first frame…'
  }
  return null
}

/** 0–100 download progress while pulling, else null. */
export function pullPercent(ws: Workspace): number | null {
  const p = ws.pull_progress
  if (!p || !p.total) return null
  return Math.min(100, Math.round((p.current / p.total) * 100))
}
