import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/workspaceFiles', () => ({
  workspaceFilesApi: { list: vi.fn(), mkdir: vi.fn(), download: vi.fn(), upload: vi.fn() },
}))

import { workspaceFilesApi } from '@/api/workspaceFiles'
import WorkspaceFilesModal from '@/components/WorkspaceFilesModal.vue'
import { useWsTransfersStore } from '@/stores/wsTransfers'
import { dropFolderFor, filesFromDrop } from '@/utils/wsDrop'
import type { Workspace } from '@/types'

const ws = { id: 4, name: 'Dev', status: 'running', workspace_type: 'desktop', ephemeral: false } as unknown as Workspace
const at = '2026-10-12T00:00:00Z'

function listing(path: string) {
  if (path === 'Desktop') return { path, entries: [{ name: 'todo.txt', type: 'file', size: 2048, modified: at }] }
  return {
    path: '',
    entries: [
      { name: 'Desktop', type: 'dir', size: 4096, modified: at },
      { name: '.cache', type: 'dir', size: 4096, modified: at },
      { name: 'notes.md', type: 'file', size: 12, modified: at },
    ],
  }
}

describe('WorkspaceFilesModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    setActivePinia(createPinia())
    vi.mocked(workspaceFilesApi.list).mockImplementation(async (_id, p = '') => listing(p) as any)
    vi.mocked(workspaceFilesApi.upload).mockImplementation(async (_id, dir, f) => ({ path: dir, name: f.name, size: f.size }))
  })
  afterEach(() => { document.body.innerHTML = '' })

  const rows = () => [...document.body.querySelectorAll<HTMLTableRowElement>('tr.row')].map(r => r.textContent!.replace(/\s+/g, ' ').trim())

  it('lists the home folder, hides dotfiles until asked, and opens folders', async () => {
    const w = mount(WorkspaceFilesModal, { props: { modelValue: true, ws }, attachTo: document.body })
    await flushPromises()
    expect(rows().some(r => r.startsWith('Desktop'))).toBe(true)
    expect(rows().some(r => r.startsWith('.cache'))).toBe(false)
    document.body.querySelector<HTMLButtonElement>('[title="Show dotfiles"]')!.click()
    await flushPromises()
    expect(rows().some(r => r.startsWith('.cache'))).toBe(true)

    document.body.querySelectorAll<HTMLTableRowElement>('tr.row')[0].click() // Desktop
    await flushPromises()
    expect(workspaceFilesApi.list).toHaveBeenLastCalledWith(4, 'Desktop')
    expect(rows()[0]).toBe('..')
    expect(rows()[1]).toContain('todo.txt')
    document.body.querySelector<HTMLButtonElement>('[title="Download todo.txt"]')!.click()
    expect(workspaceFilesApi.download).toHaveBeenCalledWith(4, 'Desktop/todo.txt')
    w.unmount()
  })

  it('uploads dropped files into the folder on screen and refreshes it', async () => {
    const w = mount(WorkspaceFilesModal, { props: { modelValue: true, ws }, attachTo: document.body })
    await flushPromises()
    const file = new File(['hello'], 'hello.txt', { type: 'text/plain' })
    const dt = { types: ['Files'], files: [file], items: undefined } as unknown as DataTransfer
    const fb = document.body.querySelector('.fb')!
    const drop = new Event('drop', { bubbles: true, cancelable: true }) as any
    drop.dataTransfer = dt
    fb.dispatchEvent(drop)
    await flushPromises()
    expect(workspaceFilesApi.upload).toHaveBeenCalledWith(4, '', file, expect.any(Function))
    const listCalls = vi.mocked(workspaceFilesApi.list).mock.calls.length
    expect(listCalls).toBeGreaterThanOrEqual(2) // refreshed after the upload finished
    expect(document.body.textContent).toContain('Uploads finished')
    w.unmount()
  })
})

describe('upload queue', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('runs two at a time and records failures', async () => {
    const resolvers: Array<() => void> = []
    vi.mocked(workspaceFilesApi.upload).mockImplementation((_id, dir, f) =>
      f.name === 'bad'
        ? Promise.reject(new Error('Over the 1 MB upload limit'))
        : new Promise(res => resolvers.push(() => res({ path: dir, name: f.name, size: f.size }))),
    )
    const store = useWsTransfersStore()
    store.enqueue(1, 'Desktop', ['a', 'b', 'c'].map(n => new File(['x'], n)))
    await flushPromises()
    expect(store.items.map(t => t.status)).toEqual(['uploading', 'uploading', 'queued'])
    resolvers.shift()!()
    await flushPromises()
    expect(store.items.map(t => t.status)).toEqual(['done', 'uploading', 'uploading'])
    store.enqueue(1, 'Desktop', [new File(['x'], 'bad')])
    resolvers.splice(0).forEach(r => r())
    await flushPromises()
    const bad = store.items.find(t => t.name === 'bad')!
    expect(bad.status).toBe('error')
    expect(bad.error).toContain('upload limit')
    store.clearDone(1)
    expect(store.items.map(t => t.name)).toEqual(['bad'])
  })
})

describe('drop helpers', () => {
  it('drops land on the desktop for desktops, Downloads otherwise', () => {
    expect(dropFolderFor({ workspace_type: 'desktop' } as Workspace)).toBe('Desktop')
    expect(dropFolderFor({ workspace_type: 'browser' } as Workspace)).toBe('Downloads')
  })

  it('leaves folders out of a drop', () => {
    const a = new File(['1'], 'a.txt')
    const items = [
      { kind: 'file', webkitGetAsEntry: () => ({ isDirectory: false }), getAsFile: () => a },
      { kind: 'file', webkitGetAsEntry: () => ({ isDirectory: true }), getAsFile: () => new File([], 'dir') },
      { kind: 'string', webkitGetAsEntry: () => null, getAsFile: () => null },
    ]
    expect(filesFromDrop({ items, files: [] } as unknown as DataTransfer)).toEqual({ files: [a], folders: 1 })
    expect(filesFromDrop(null)).toEqual({ files: [], folders: 0 })
  })
})
