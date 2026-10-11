import { describe, it, expect } from 'vitest'
import {
  autoStopAt,
  displayStatus,
  fmtDuration,
  parseTs,
  pullPercent,
  relativeTime,
  startingDetail,
  statusLabel,
} from '@/utils/workspaceStatus'
import type { Workspace } from '@/types'

const ws = (over: Partial<Workspace>) =>
  ({ id: 1, status: 'running', connectable: true, workspace_type: 'desktop', container_id: 'c', pull_progress: null, ...over }) as Workspace

describe('workspaceStatus', () => {
  it('uses one label per state', () => {
    expect(statusLabel('running')).toBe('Running')
    expect(statusLabel('stopping')).toBe('Stopping')
    expect(statusLabel('stopped')).toBe('Stopped')
    expect(statusLabel('creating')).toBe('Starting')
  })

  it('shows a running-but-not-connectable workspace as Starting', () => {
    expect(displayStatus(ws({ connectable: false }))).toBe('creating')
    expect(displayStatus(ws({}))).toBe('running')
  })

  it('parses naive API timestamps as UTC', () => {
    expect(parseTs('2026-10-11T19:48:46')).toBe(Date.UTC(2026, 9, 11, 19, 48, 46))
    expect(parseTs('2026-10-11T19:48:46Z')).toBe(Date.UTC(2026, 9, 11, 19, 48, 46))
    expect(parseTs('2026-10-11T21:48:46+02:00')).toBe(Date.UTC(2026, 9, 11, 19, 48, 46))
    expect(parseTs(null)).toBeNull()
  })

  it('formats durations and relative times', () => {
    expect(fmtDuration(30_000)).toBe('30s')
    expect(fmtDuration(12 * 60_000)).toBe('12 min')
    expect(fmtDuration(185 * 60_000)).toBe('3 h 5 min')
    expect(fmtDuration(50 * 3_600_000)).toBe('2 d')
    const now = Date.UTC(2026, 0, 1, 12)
    expect(relativeTime(now - 10_000, now)).toBe('just now')
    expect(relativeTime(now - 5 * 60_000, now)).toBe('5 min ago')
  })

  it('computes the auto-stop time from the later of start and extension', () => {
    const started = '2026-01-01T08:00:00'
    expect(autoStopAt(ws({ started_at: started, runtime_extended_at: null }), 24)).toBe(Date.UTC(2026, 0, 2, 8))
    expect(autoStopAt(ws({ started_at: started, runtime_extended_at: '2026-01-01T10:00:00' }), 24)).toBe(
      Date.UTC(2026, 0, 2, 10),
    )
    expect(autoStopAt(ws({ started_at: started, runtime_extended_at: null }), 0)).toBeNull()
    expect(autoStopAt(ws({ status: 'stopped', started_at: started, runtime_extended_at: null }), 24)).toBeNull()
  })

  it('explains what a starting workspace is waiting on', () => {
    const pulling = ws({ status: 'creating', container_id: null, pull_progress: { current: 512 * 1024 ** 2, total: 2 * 1024 ** 3, layers: 4 } })
    expect(startingDetail(pulling)).toBe('Downloading image · 512 MB / 2.0 GB')
    expect(pullPercent(pulling)).toBe(25)
    expect(startingDetail(ws({ status: 'creating', container_id: null }))).toBe('Preparing…')
    expect(startingDetail(ws({ status: 'creating' }))).toBe('Starting container…')
    expect(startingDetail(ws({ connectable: false }))).toBe('Waiting for the desktop to draw…')
    expect(startingDetail(ws({}))).toBeNull()
  })
})
