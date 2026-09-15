import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { ProotApps, Workspace } from '@/types'

vi.mock('@/api/proot', () => ({
  prootApi: { list: vi.fn(), installed: vi.fn(), startTask: vi.fn(), allTasks: vi.fn(), clearTasks: vi.fn(), taskLog: vi.fn() },
}))
vi.mock('@/api/workspaces', () => ({
  workspacesApi: { get: vi.fn() },
}))
const toastMock = vi.fn()
vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({ toast: toastMock }),
}))
vi.mock('@/components/BaseModal.vue', () => ({
  default: { props: ['modelValue'], template: '<div v-if="modelValue"><slot /></div>' },
}))

import { prootApi } from '@/api/proot'
import { workspacesApi } from '@/api/workspaces'
import ProotAppsModal from '@/components/ProotAppsModal.vue'

const ws = { id: 5, name: 'desk', status: 'running', workspace_type: 'desktop' } as Workspace
const digest = (c: string) => 'sha256:' + c.repeat(64)

const listing: ProotApps = {
  available: true,
  arch: 'amd64',
  checked: true,
  check_failed: false,
  apps: [
    { name: 'firefox', full_name: 'Mozilla Firefox', icon_url: 'https://raw.githubusercontent.com/x/firefox.svg', folder: 'f', installed: true, downloading: false, in_config: true,
      installed_digest: digest('a'), latest_digest: digest('b'), update_available: true },
    { name: 'gimp', full_name: null, icon_url: null, folder: 'g', installed: true, downloading: false, in_config: true,
      installed_digest: digest('a'), latest_digest: digest('a'), update_available: false },
    { name: 'blender', full_name: null, icon_url: null, folder: '', installed: false, downloading: false, in_config: true,
      installed_digest: null, latest_digest: null, update_available: null },
  ],
}

async function mountOpen() {
  const wrapper = mount(ProotAppsModal, { props: { ws, modelValue: false }, attachTo: document.body })
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  return wrapper
}

describe('ProotAppsModal', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(prootApi.installed).mockResolvedValue(listing)
    vi.mocked(prootApi.allTasks).mockResolvedValue([])
    vi.mocked(prootApi.list).mockResolvedValue({ apps: [] })
    vi.mocked(prootApi.startTask).mockResolvedValue({} as any)
    vi.mocked(workspacesApi.get).mockResolvedValue(ws)
  })

  it('shows each app with its update status', async () => {
    const wrapper = await mountOpen()
    expect(prootApi.installed).toHaveBeenCalledWith(5)
    const text = wrapper.text()
    expect(text).toContain('update available')
    expect(text).toContain('up to date')
    expect(text).toContain('not installed')
    expect(text).toContain('1 update')
  })

  it('shows a spinner while the app list loads', async () => {
    let resolve!: (v: ProotApps) => void
    vi.mocked(prootApi.installed).mockImplementationOnce(() => new Promise(r => { resolve = r }))
    const wrapper = mount(ProotAppsModal, { props: { ws, modelValue: false }, attachTo: document.body })
    await wrapper.setProps({ modelValue: true })
    expect(wrapper.find('[role="status"] .ring').exists()).toBe(true)
    resolve(listing)
    await flushPromises()
    expect(wrapper.find('.app-list [role="status"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('firefox')
  })

  it('shows app icons, falling back to a generic glyph', async () => {
    const wrapper = await mountOpen()
    const imgs = wrapper.findAll('.app-row img')
    expect(imgs).toHaveLength(1)
    expect(imgs[0].attributes('src')).toBe('https://raw.githubusercontent.com/x/firefox.svg')
    expect(imgs[0].attributes('referrerpolicy')).toBe('no-referrer')
    // A broken image swaps to the fallback instead of rendering broken.
    await imgs[0].trigger('error')
    expect(wrapper.findAll('.app-row img')).toHaveLength(0)
    expect(wrapper.findAll('.app-row .app-icon svg')).toHaveLength(3)
  })

  it('updates one app, and all apps with updates', async () => {
    const wrapper = await mountOpen()
    const buttons = wrapper.findAll('button')
    await buttons.find(b => b.text() === 'Update all')!.trigger('click')
    await flushPromises()
    expect(prootApi.startTask).toHaveBeenCalledWith(5, 'update', ['firefox'])
    // An update doesn't change the saved list, so the workspace isn't refetched.
    expect(workspacesApi.get).not.toHaveBeenCalled()
  })

  it('installs a saved-but-missing app and refreshes the workspace', async () => {
    const wrapper = await mountOpen()
    const install = wrapper.findAll('button').find(b => b.text() === 'Install')!
    await install.trigger('click')
    await flushPromises()
    expect(prootApi.startTask).toHaveBeenCalledWith(5, 'install', ['blender'])
    expect(workspacesApi.get).toHaveBeenCalledWith(5)
  })

  it('hides actions for apps in a running task', async () => {
    vi.mocked(prootApi.allTasks).mockResolvedValue([{
      workspace_id: 5, workspace_name: 'desk',
      tasks: [{ id: 't', op: 'update', state: 'running', exit_code: null, apps: ['firefox'], failed_apps: [],
        current_app: 'firefox', done_count: 0, created_at: 1, started_at: 1, finished_at: null }],
    }])
    const wrapper = await mountOpen()
    await flushPromises()
    expect(wrapper.text()).toContain('updating…')
    expect(wrapper.findAll('button').some(b => b.text() === 'Update all')).toBe(false)
  })

  it('surfaces a failure to start as an error toast', async () => {
    vi.mocked(prootApi.startTask).mockRejectedValue(new Error('Too many app tasks'))
    const wrapper = await mountOpen()
    await wrapper.findAll('button').find(b => b.text() === 'Update')!.trigger('click')
    await flushPromises()
    expect(toastMock).toHaveBeenCalledWith('Too many app tasks', 'error')
  })
})
