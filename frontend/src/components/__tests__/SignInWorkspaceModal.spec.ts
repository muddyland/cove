import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { User } from '@/types'

const workspaces = vi.fn()
const setAutoLaunch = vi.fn()
const createWorkspace = vi.fn()
vi.mock('@/api/admin', () => ({
  adminApi: { users: { workspaces: (...a: unknown[]) => workspaces(...a), setAutoLaunch: (...a: unknown[]) => setAutoLaunch(...a), createWorkspace: (...a: unknown[]) => createWorkspace(...a) } },
}))
vi.mock('@/api/images', () => ({
  imagesApi: { list: vi.fn().mockResolvedValue([
    { id: 3, name: 'Ubuntu', image_type: 'desktop', url_env: null, enabled: true },
    { id: 4, name: 'Old', image_type: 'desktop', url_env: null, enabled: false },
  ]) },
}))
vi.mock('@/api/zones', () => ({
  zonesApi: { list: vi.fn().mockResolvedValue([{ id: 0, name: 'local', status: 'enrolled' }, { id: 2, name: 'Rack', status: 'enrolled' }]) },
}))
vi.mock('@/components/BaseModal.vue', () => ({
  default: { props: ['modelValue'], template: '<div v-if="modelValue"><slot /></div>' },
}))

import SignInWorkspaceModal from '@/components/SignInWorkspaceModal.vue'

const bob = {
  id: 2, username: 'bob', is_admin: false, zones_restricted: true, allowed_zone_ids: [2],
  auto_launch_workspace_id: null, auto_launch_locked: false,
} as unknown as User

describe('SignInWorkspaceModal', () => {
  beforeEach(() => vi.clearAllMocks())

  it('starts on the create form for an account with no workspaces, and creates a locked one', async () => {
    workspaces.mockResolvedValue([])
    createWorkspace.mockResolvedValue({ id: 9, public_id: 'new-ws' })
    const wrapper = mount(SignInWorkspaceModal, { props: { modelValue: true, user: bob } })
    await flushPromises()
    expect((wrapper.find('#sw-choice').element as HTMLSelectElement).value).toBe('__new__')
    // Only enabled images; only the zone bob may use (so no zone picker).
    expect(wrapper.findAll('#sw-image option').map(o => o.text())).toEqual(['— choose —', 'Ubuntu'])
    expect(wrapper.find('#sw-zone').exists()).toBe(false)

    await wrapper.find('#sw-name').setValue('Desk')
    await wrapper.find('#sw-image').setValue('3')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(createWorkspace).toHaveBeenCalledWith(2, {
      name: 'Desk', image_id: 3, zone_id: 2, target_url: null, auto_launch: true, locked: true,
    })
    expect(wrapper.emitted('saved')![0][0]).toMatchObject({ auto_launch_workspace_id: 'new-ws', auto_launch_locked: true })
  })

  it('picks an existing workspace, and never locks the dashboard', async () => {
    workspaces.mockResolvedValue([
      { public_id: 'a', name: 'A', image_name: 'Ubuntu', status: 'stopped', auto_remove: false },
      { public_id: 'tmp', name: 'Tmp', image_name: 'Ubuntu', status: 'running', auto_remove: true },
    ])
    setAutoLaunch.mockResolvedValue({ ...bob, auto_launch_workspace_id: 'a' })
    const wrapper = mount(SignInWorkspaceModal, { props: { modelValue: true, user: bob } })
    await flushPromises()
    // A self-deleting workspace isn't offered.
    expect(wrapper.findAll('#sw-choice option').map(o => o.attributes('value'))).toEqual(['', 'a', '__new__'])
    expect((wrapper.find('input[type="checkbox"]').element as HTMLInputElement).disabled).toBe(true)

    await wrapper.find('#sw-choice').setValue('a')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(setAutoLaunch).toHaveBeenCalledWith(2, 'a', false)
  })
})
