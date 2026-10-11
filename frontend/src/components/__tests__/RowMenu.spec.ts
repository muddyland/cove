import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import RowMenu from '@/components/RowMenu.vue'

const mk = () => {
  const copy = vi.fn()
  const del = vi.fn()
  const wrapper = mount(RowMenu, {
    attachTo: document.body,
    props: {
      label: 'Actions for a.txt',
      items: [
        { key: 'copy', label: 'Copy', action: copy },
        { key: 'off', label: 'Unavailable', disabled: true, action: vi.fn() },
        { key: 'del', label: 'Delete permanently…', danger: true, separator: true, action: del },
      ],
    },
  })
  return { wrapper, copy, del }
}
const menu = () => document.body.querySelector<HTMLElement>('.row-menu')
const items = () => [...document.body.querySelectorAll<HTMLButtonElement>('.row-menu-item')]

afterEach(() => {
  document.body.innerHTML = ''
})

describe('RowMenu', () => {
  it('opens from the ⋯ button, runs an item and closes', async () => {
    const { wrapper, copy } = mk()
    expect(menu()).toBeNull()
    await wrapper.get('button.row-menu-trigger').trigger('click')
    expect(menu()).not.toBeNull()
    expect(wrapper.get('button.row-menu-trigger').attributes('aria-expanded')).toBe('true')
    expect(items().map(b => b.textContent?.trim())).toEqual(['Copy', 'Unavailable', 'Delete permanently…'])
    expect(document.body.querySelectorAll('.row-menu-sep')).toHaveLength(1)
    items()[0].click()
    await nextTick()
    expect(copy).toHaveBeenCalledOnce()
    expect(menu()).toBeNull()
    wrapper.unmount()
  })

  it('ignores disabled items and closes on Escape and outside clicks', async () => {
    const { wrapper } = mk()
    await wrapper.get('button.row-menu-trigger').trigger('click')
    expect(items()[1].disabled).toBe(true)
    menu()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(menu()).toBeNull()

    await wrapper.get('button.row-menu-trigger').trigger('click')
    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(menu()).toBeNull()
    wrapper.unmount()
  })

  it('moves focus with the arrow keys, skipping disabled items', async () => {
    const { wrapper } = mk()
    await wrapper.get('button.row-menu-trigger').trigger('click')
    await nextTick()
    expect(document.activeElement?.textContent?.trim()).toBe('Copy')
    menu()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    expect(document.activeElement?.textContent?.trim()).toBe('Delete permanently…')
    menu()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    expect(document.activeElement?.textContent?.trim()).toBe('Copy')
    wrapper.unmount()
  })

  it('opens at the cursor for a right-click', async () => {
    const { wrapper } = mk()
    const e = new MouseEvent('contextmenu', { clientX: 40, clientY: 60, cancelable: true })
    ;(wrapper.vm as unknown as { openAt: (e: MouseEvent) => void }).openAt(e)
    await nextTick()
    await nextTick()
    expect(e.defaultPrevented).toBe(true)
    expect(menu()!.style.top).toBe('60px')
    expect(menu()!.style.left).toBe('40px')
    wrapper.unmount()
  })

  it('keeps only one menu open at a time', async () => {
    const a = mk()
    const b = mk()
    await a.wrapper.get('button.row-menu-trigger').trigger('click')
    await b.wrapper.get('button.row-menu-trigger').trigger('click')
    await nextTick()
    expect(document.body.querySelectorAll('.row-menu')).toHaveLength(1)
    expect(a.wrapper.get('button.row-menu-trigger').attributes('aria-expanded')).toBe('false')
    a.wrapper.unmount()
    b.wrapper.unmount()
  })
})
