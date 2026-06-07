import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { Workspace } from '@/types'

vi.mock('@/api/proot', () => ({
  prootApi: { list: vi.fn() },
}))

vi.mock('@/api/workspaces', () => ({
  workspacesApi: { lanPolicy: vi.fn() },
}))

const updateMock = vi.fn()
vi.mock('@/stores/workspaces', () => ({
  useWorkspacesStore: () => ({ update: updateMock }),
}))

const toastMock = vi.fn()
vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({ toast: toastMock }),
}))

// BaseModal just renders its default slot.
vi.mock('@/components/BaseModal.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

import { prootApi } from '@/api/proot'
import { workspacesApi } from '@/api/workspaces'
import EditWorkspaceModal from '@/components/EditWorkspaceModal.vue'

const desktopWs: Workspace = {
  id: 42,
  public_id: 'abc',
  user_id: 1,
  name: 'My Desktop',
  status: 'stopped',
  workspace_type: 'desktop',
  container_id: null,
  container_name: null,
  image_id: 7,
  image_name: 'Ubuntu Desktop',
  image_logo: null,
  target_url: null,
  kiosk: false,
  kiosk_dark: false,
  kiosk_menu: false,
  stream_url: null,
  created_at: '2026-01-01T00:00:00Z',
  started_at: null,
  stopped_at: null,
  error_message: null,
  use_tailscale: false,
  ephemeral: false,
  lan_access: false,
  ts_exit_node: null,
  ts_accept_routes: true,
  ts_accept_dns: true,
  custom_dns: false,
  dns_servers: null,
  install_packages: null,
  proot_apps: null,
  appimages: null,
  allow_sudo: true,
}

describe('EditWorkspaceModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    vi.mocked(prootApi.list).mockResolvedValue({ apps: ['firefox'] })
    vi.mocked(workspacesApi.lanPolicy).mockResolvedValue({ enabled: false, subnets: [] })
    updateMock.mockResolvedValue({ ...desktopWs })
  })

  it('pre-fills from the workspace and sends changed fields on save', async () => {
    const wrapper = mount(EditWorkspaceModal, {
      props: { modelValue: true, ws: desktopWs },
    })
    await flushPromises()

    const nameInput = wrapper.find('input').element as HTMLInputElement
    expect(nameInput.value).toBe('My Desktop')

    await wrapper.find('input').setValue('Renamed')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(updateMock).toHaveBeenCalledOnce()
    const [id, payload] = updateMock.mock.calls[0]
    expect(id).toBe(42)
    expect(payload.name).toBe('Renamed')
    expect(payload.use_tailscale).toBe(false)
    expect(payload).not.toHaveProperty('ts_exit_node')
    expect(toastMock).toHaveBeenCalledWith('Workspace updated', 'success')
  })
})
