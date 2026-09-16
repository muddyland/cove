import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import type { ProotApps, Workspace } from '@/types'

vi.mock('@/api/proot', () => ({
  prootApi: {
    list: vi.fn(), installed: vi.fn(), startTask: vi.fn(), allTasks: vi.fn(), clearTasks: vi.fn(), taskLog: vi.fn(),
    appImages: vi.fn(), installAppImages: vi.fn(), updateAppImage: vi.fn(), removeAppImages: vi.fn(),
  },
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
    vi.mocked(prootApi.appImages).mockResolvedValue({ apps: [] })
    vi.mocked(prootApi.installAppImages).mockResolvedValue({} as any)
    vi.mocked(prootApi.updateAppImage).mockResolvedValue({} as any)
    vi.mocked(prootApi.removeAppImages).mockResolvedValue({} as any)
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
      tasks: [{ id: 't', kind: 'proot', op: 'update', state: 'running', exit_code: null, apps: ['firefox'], failed_apps: [],
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

describe('AppImages section', () => {
  const img = {
    slug: 'Foo-1.2_x86_64',
    name: 'Foo',
    url: 'https://apps.example.com/Foo-1.2_x86_64.AppImage',
    size_kb: 2048,
    installed_at: 1789500000,
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(prootApi.installed).mockResolvedValue(listing)
    vi.mocked(prootApi.allTasks).mockResolvedValue([])
    vi.mocked(prootApi.list).mockResolvedValue({ apps: [] })
    vi.mocked(workspacesApi.get).mockResolvedValue(ws)
    vi.mocked(prootApi.appImages).mockResolvedValue({ apps: [img] })
    vi.mocked(prootApi.installAppImages).mockResolvedValue({} as any)
    vi.mocked(prootApi.updateAppImage).mockResolvedValue({} as any)
    vi.mocked(prootApi.removeAppImages).mockResolvedValue({} as any)
  })

  it('lists installed AppImages with their size and source', async () => {
    const wrapper = await mountOpen()
    expect(prootApi.appImages).toHaveBeenCalledWith(5)
    const text = wrapper.text()
    expect(text).toContain('Foo')
    expect(text).toContain('2 MB')
    expect(text).toContain('Foo-1.2_x86_64.AppImage')
  })

  it('installs from pasted URLs and rejects bad ones before calling the server', async () => {
    const wrapper = await mountOpen()
    const section = wrapper.get('[data-test="appimages"]')
    await section.findAll('button').find(b => b.text().includes('Add'))!.trigger('click')
    const box = section.find('textarea')

    await box.setValue('ftp://x.io/A.AppImage')
    await section.findAll('button').find(b => b.text().includes('Install'))!.trigger('click')
    await flushPromises()
    expect(prootApi.installAppImages).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Not a usable AppImage URL')

    await box.setValue('https://x.io/A.AppImage\nhttps://x.io/B.AppImage')
    await section.findAll('button').find(b => b.text().includes('Install'))!.trigger('click')
    await flushPromises()
    expect(prootApi.installAppImages).toHaveBeenCalledWith(5, ['https://x.io/A.AppImage', 'https://x.io/B.AppImage'])
  })

  it('updates one by URL, prefilled with where it came from', async () => {
    const wrapper = await mountOpen()
    const section = wrapper.get('[data-test="appimages"]')
    await section.findAll('button').find(b => b.text() === 'Update')!.trigger('click')
    await flushPromises()
    const input = wrapper.findAll('input').find(i => (i.element as HTMLInputElement).type === 'url')!
    expect((input.element as HTMLInputElement).value).toBe(img.url)

    await input.setValue('https://apps.example.com/Foo-1.3_x86_64.AppImage')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(prootApi.updateAppImage).toHaveBeenCalledWith(5, img.slug, 'https://apps.example.com/Foo-1.3_x86_64.AppImage')
  })

  it('removes one after confirming', async () => {
    const wrapper = await mountOpen()
    const section = wrapper.get('[data-test="appimages"]')
    await section.findAll('button').find(b => b.text() === 'Remove')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === 'REMOVE')!.trigger('click')
    await flushPromises()
    expect(prootApi.removeAppImages).toHaveBeenCalledWith(5, [img.slug])
  })
})
