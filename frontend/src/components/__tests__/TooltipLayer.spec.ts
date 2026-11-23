import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import TooltipLayer from '@/components/TooltipLayer.vue'

function pointer(type: string, target: Element, init: Record<string, unknown> = {}) {
  const e = new Event(type, { bubbles: true }) as Event & Record<string, unknown>
  Object.assign(e, { pointerType: 'mouse', relatedTarget: null, ...init })
  target.dispatchEvent(e)
}

describe('TooltipLayer', () => {
  let layer: VueWrapper
  let host: HTMLElement

  beforeEach(() => {
    vi.useFakeTimers()
    document.body.innerHTML = ''
    host = document.createElement('div')
    host.innerHTML = `
      <button id="a" title="Start the workspace"><svg id="icon"></svg></button>
      <div id="outer" title="Outer"><span id="inner" title="Inner"></span><span id="plain"></span></div>
      <iframe id="frame" title="Stream"></iframe>
      <div data-no-tooltip><button id="quiet" title="Quiet"></button></div>`
    document.body.appendChild(host)
    layer = mount(TooltipLayer, { attachTo: document.body })
  })
  afterEach(() => {
    layer.unmount()
    vi.useRealTimers()
  })

  const tip = () => document.getElementById('cove-tooltip') as HTMLElement
  const shown = () => tip().style.display !== 'none'
  const $ = (id: string) => document.getElementById(id)!

  it('draws the title instead of the native tooltip, and puts it back on leave', async () => {
    pointer('pointerover', $('icon'))
    // Lifted at once so the browser's own tooltip never starts its timer.
    expect($('a').hasAttribute('title')).toBe(false)
    expect($('a').getAttribute('aria-describedby')).toBe('cove-tooltip')
    expect(shown()).toBe(false)
    vi.advanceTimersByTime(200)
    await nextTick()
    expect(shown()).toBe(true)
    expect(tip().textContent).toBe('Start the workspace')

    // Moving within the button keeps it.
    pointer('pointerout', $('icon'), { relatedTarget: $('a') })
    expect(shown()).toBe(true)

    pointer('pointerout', $('a'), { relatedTarget: document.body })
    await nextTick()
    expect(shown()).toBe(false)
    expect($('a').getAttribute('title')).toBe('Start the workspace')
    expect($('a').hasAttribute('aria-describedby')).toBe(false)
  })

  it('follows a title that changes while showing', async () => {
    pointer('pointerover', $('a'))
    vi.advanceTimersByTime(200)
    $('a').setAttribute('title', 'Restart')  // what Vue does on a re-render
    await Promise.resolve()
    await nextTick()
    expect(tip().textContent).toBe('Restart')
    expect($('a').hasAttribute('title')).toBe(false)
    pointer('pointerout', $('a'), { relatedTarget: document.body })
    expect($('a').getAttribute('title')).toBe('Restart')
  })

  it('lets a nested title win, then hands back to the outer one', async () => {
    pointer('pointerover', $('inner'))
    vi.advanceTimersByTime(200)
    await nextTick()
    expect(tip().textContent).toBe('Inner')
    expect($('outer').getAttribute('title')).toBe('Outer')
    pointer('pointerover', $('plain'))
    vi.advanceTimersByTime(200)
    await nextTick()
    expect(tip().textContent).toBe('Outer')
    expect($('inner').getAttribute('title')).toBe('Inner')
  })

  it('leaves touch, iframes and opted-out elements alone, and hides on click', async () => {
    pointer('pointerover', $('a'), { pointerType: 'touch' })
    expect($('a').getAttribute('title')).toBe('Start the workspace')
    pointer('pointerover', $('frame'))
    expect($('frame').getAttribute('title')).toBe('Stream')
    pointer('pointerover', $('quiet'))
    expect($('quiet').getAttribute('title')).toBe('Quiet')

    pointer('pointerover', $('a'))
    vi.advanceTimersByTime(200)
    await nextTick()
    expect(shown()).toBe(true)
    pointer('pointerdown', $('a'))
    await nextTick()
    expect(shown()).toBe(false)
    // Still lifted while the pointer is on it: no native tooltip either.
    expect($('a').hasAttribute('title')).toBe(false)
  })
})
