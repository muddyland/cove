import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { DocContent, DocEntry } from '@/api/docs'

vi.mock('@/api/docs', () => ({
  docsApi: { list: vi.fn(), get: vi.fn() },
}))

// BaseModal just renders its default slot.
vi.mock('@/components/BaseModal.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

import { docsApi } from '@/api/docs'
import DocsModal from '@/components/DocsModal.vue'

const USER_DOCS: DocEntry[] = [
  { slug: 'README', title: 'Cove Documentation', scope: 'user' },
  { slug: 'workspaces', title: 'Workspaces', scope: 'user' },
]
const ADMIN_DOC: DocEntry = { slug: 'security', title: 'Security model', scope: 'admin' }

function doc(slug: string, content: string, scope: 'user' | 'admin' = 'user'): DocContent {
  return { slug, title: slug, scope, content }
}

async function open(entries: DocEntry[], initial?: string) {
  vi.mocked(docsApi.list).mockResolvedValue(entries)
  const wrapper = mount(DocsModal, {
    props: { modelValue: false, ...(initial ? { initial } : {}) },
  })
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(docsApi.get).mockImplementation(async (slug: string) => doc(slug, `# ${slug}\n\nBody.`))
})

describe('DocsModal', () => {
  it('lists documents and lands on the quick reference first', async () => {
    const wrapper = await open(USER_DOCS)

    const items = wrapper.findAll('.rail-item').map(b => b.text())
    expect(items[0]).toBe('Quick help')
    expect(items).toContain('Workspaces')
    // The quick reference is a component, not fetched Markdown.
    expect(docsApi.get).not.toHaveBeenCalled()
    expect(wrapper.find('.quick').exists()).toBe(true)
  })

  it('fetches and renders a document when a sidebar entry is clicked', async () => {
    const wrapper = await open(USER_DOCS)
    vi.mocked(docsApi.get).mockResolvedValue(doc('workspaces', '# Workspaces\n\nLaunch options.'))

    await wrapper.findAll('.rail-item').find(b => b.text() === 'Workspaces')!.trigger('click')
    await flushPromises()

    expect(docsApi.get).toHaveBeenCalledWith('workspaces')
    expect(wrapper.find('.markdown-body').html()).toContain('Launch options.')
  })

  it('groups admin-scoped docs separately and badges the open one', async () => {
    const wrapper = await open([...USER_DOCS, ADMIN_DOC])

    const heads = wrapper.findAll('.group-head').map(h => h.text())
    expect(heads).toEqual(['USING COVE', 'OPERATING COVE'])

    vi.mocked(docsApi.get).mockResolvedValue(doc('security', '# Security', 'admin'))
    await wrapper.findAll('.rail-item').find(b => b.text() === 'Security model')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.scope-tag').text()).toContain('ADMIN')
  })

  it('omits the admin group entirely when the API returns no admin docs', async () => {
    const wrapper = await open(USER_DOCS)
    expect(wrapper.findAll('.group-head').map(h => h.text())).toEqual(['USING COVE'])
  })

  it('filters the sidebar by search query', async () => {
    const wrapper = await open(USER_DOCS)

    await wrapper.find('.search-input').setValue('workspa')
    expect(wrapper.findAll('.rail-item').map(b => b.text())).toEqual(['Workspaces'])

    await wrapper.find('.search-input').setValue('nothing-here')
    expect(wrapper.find('.rail-empty').exists()).toBe(true)
  })

  it('resolves cross-document .md links inside the modal', async () => {
    const wrapper = await open(USER_DOCS)
    vi.mocked(docsApi.get).mockResolvedValue(
      doc('README', '# Index\n\n[Workspaces](workspaces.md#gpu-acceleration)'),
    )
    await wrapper.findAll('.rail-item').find(b => b.text() === 'Cove Documentation')!.trigger('click')
    await flushPromises()

    vi.mocked(docsApi.get).mockResolvedValue(doc('workspaces', '# Workspaces'))
    await wrapper.find('.markdown-body a').trigger('click')
    await flushPromises()

    expect(docsApi.get).toHaveBeenLastCalledWith('workspaces')
    // Following a cross-link exposes the Back control.
    expect(wrapper.find('.back-btn').exists()).toBe(true)
  })

  it('honours the initial prop so the workspace screen opens the quick reference', async () => {
    const wrapper = await open(USER_DOCS, '__quick')
    expect(wrapper.find('.quick').exists()).toBe(true)
    expect(docsApi.get).not.toHaveBeenCalled()
  })

  it('explains a 403 when a non-admin follows a cross-link to an operator page', async () => {
    const wrapper = await open(USER_DOCS)
    const denied = Object.assign(new Error('Admin required'), { status: 403 })
    vi.mocked(docsApi.get).mockRejectedValue(denied)

    await wrapper.findAll('.rail-item').find(b => b.text() === 'Workspaces')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.state.error').text()).toContain('needs an admin account')
  })

  it('surfaces a load failure instead of rendering an empty pane', async () => {
    vi.mocked(docsApi.list).mockRejectedValue(new Error('nope'))
    const wrapper = mount(DocsModal, { props: { modelValue: false } })
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(wrapper.find('.state.error').text()).toContain('nope')
  })
})
