import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { User } from '@/types'

vi.mock('@/api/zones', () => ({ zonesApi: { list: vi.fn().mockResolvedValue([]) } }))
vi.mock('@/components/BaseModal.vue', () => ({
  default: { props: ['modelValue'], template: '<div v-if="modelValue"><slot /></div>' },
}))

import UserFormModal from '@/components/UserFormModal.vue'

const bob: User = {
  id: 2, username: 'bob', is_admin: false, docker_allowed: false, zones_restricted: false,
  allowed_zone_ids: [], max_workspaces: 3, max_running: null, lan_only: false, auth_provider: 'local',
  created_at: '2026-10-01T00:00:00Z', last_login_at: null,
} as User

function mountForm(editUser: User | null) {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  const wrapper = mount(UserFormModal, { props: { modelValue: true, editUser, onSubmit } })
  return { wrapper, onSubmit }
}

const limitInputs = (w: ReturnType<typeof mount>) => w.findAll('.limits input[type="number"]')

describe('UserFormModal workspace limits', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the account override, and empty for "follow the default"', async () => {
    const { wrapper } = mountForm(bob)
    await flushPromises()
    const [total, running] = limitInputs(wrapper)
    expect((total.element as HTMLInputElement).value).toBe('3')
    expect((running.element as HTMLInputElement).value).toBe('')
  })

  it('sends null for empty, 0 for no limit, N for a cap', async () => {
    const { wrapper, onSubmit } = mountForm(bob)
    await flushPromises()
    const [total, running] = limitInputs(wrapper)
    await total.setValue('')
    await running.setValue('0')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ max_workspaces: null, max_running: 0 }))

    const second = mountForm(bob)
    await flushPromises()
    await limitInputs(second.wrapper)[0].setValue('5')
    await second.wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(second.onSubmit).toHaveBeenCalledWith(expect.objectContaining({ max_workspaces: 5, max_running: null }))
  })

  it('sends the Local network only switch', async () => {
    const { wrapper, onSubmit } = mountForm(bob)
    await flushPromises()
    const box = wrapper.findAll('input[type="checkbox"]').find(c => c.element.parentElement?.textContent?.includes('Local network only'))!
    await box.setValue(true)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ lan_only: true }))
  })

  it('disables the limits for admins, who are never limited', async () => {
    const { wrapper } = mountForm({ ...bob, is_admin: true })
    await flushPromises()
    expect(wrapper.find('fieldset.limits').attributes('disabled')).toBeDefined()
    expect(wrapper.find('fieldset.limits').text()).toContain('Admins are never limited')
  })
})
