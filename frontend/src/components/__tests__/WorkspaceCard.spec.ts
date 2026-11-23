import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import type { Workspace } from '@/types'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

import WorkspaceCard from '@/components/WorkspaceCard.vue'
import { useWorkspacesStore } from '@/stores/workspaces'

function ws(over: Partial<Workspace> = {}): Workspace {
  return {
    id: 1, public_id: 'p1', user_id: 1, name: 'Desk', status: 'running', workspace_type: 'desktop',
    zone_id: 0, zone_name: 'Local', image_id: 1, image_name: 'Ubuntu', image_logo: null,
    connectable: true, ephemeral: false, auto_remove: false, use_gluetun: false, use_tailscale: false,
    created_at: '2026-11-23T10:00:00', started_at: '2026-11-23T10:00:00', stopped_at: null,
    error_message: null, preview_at: null, favicon_at: null, pull_progress: null, runtime_extended_at: null,
    ...over,
  } as unknown as Workspace
}

const STUBS = {
  EditWorkspaceModal: true, CloneModal: true, MigrateModal: true, DiagnosticsModal: true,
  ProotAppsModal: true,
  ConfirmModal: { name: 'ConfirmModal', props: ['modelValue', 'title', 'message'], template: '<div />' },
}

function mountCard(w: Workspace) {
  return mount(WorkspaceCard, { props: { ws: w }, global: { stubs: STUBS } })
}

describe('WorkspaceCard actions row', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('shows icon-only Connect, Stop and Actions on a running workspace', () => {
    const card = mountCard(ws())
    const buttons = card.findAll('.card-actions > .btn, .card-actions .actions-trigger')
    expect(buttons.map(b => b.attributes('aria-label'))).toEqual(['Connect', 'Stop', 'Actions'])
    // Their tooltips (titles, drawn by TooltipLayer).
    expect(buttons.map(b => b.attributes('title'))).toEqual(['Connect', 'Stop', 'Actions'])
    for (const b of buttons) expect(b.text().trim()).toBe('')
    expect(buttons[0].classes()).toContain('half-action')
    expect(buttons[1].classes()).toContain('half-action')
    // Stop isn't offered twice.
    expect(card.findAll('.action-item').map(b => b.text())).not.toContain('Stop')
  })

  it('stops a plain workspace at once, and asks first when that would lose something', async () => {
    const plain = mountCard(ws())
    const stop = vi.spyOn(useWorkspacesStore(), 'stop').mockResolvedValue(undefined as never)
    await plain.find('.stop-action').trigger('click')
    expect(stop).toHaveBeenCalledWith(1)

    stop.mockClear()
    const busy = mountCard(ws({ in_use_by: 'Desk (thin client)', ephemeral: true }))
    await busy.find('.stop-action').trigger('click')
    expect(stop).not.toHaveBeenCalled()
    const confirm = busy.findAllComponents({ name: 'ConfirmModal' }).find(m => m.props('title') === 'Stop workspace')!
    expect(confirm.props('modelValue')).toBe(true)
    expect(confirm.props('message')).toContain('open on Desk (thin client)')
    expect(confirm.props('message')).toContain('ephemeral')
    confirm.vm.$emit('confirm')
    await Promise.resolve()
    expect(stop).toHaveBeenCalledWith(1)
  })

  it('names Start and Restart the same way', () => {
    const stopped = mountCard(ws({ status: 'stopped', connectable: false }))
    expect(stopped.find('.primary-action').attributes('aria-label')).toBe('Start')
    expect(stopped.find('.stop-action').exists()).toBe(false)
    expect(mountCard(ws({ status: 'error', connectable: false })).find('.primary-action').attributes('title')).toBe('Restart')
  })

  it('says where a running workspace is in use, and nothing otherwise', () => {
    const inUse = mountCard(ws({ in_use_by: 'Desk (thin client)', in_use_since: '2026-11-23T10:05:00' }))
    expect(inUse.find('.in-use').text()).toBe('In use by Desk (thin client)')
    expect(mountCard(ws()).find('.in-use').exists()).toBe(false)
    expect(mountCard(ws({ status: 'stopped', in_use_by: 'stale' })).find('.in-use').exists()).toBe(false)
  })
})
