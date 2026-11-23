<template>
  <div
    v-show="visible"
    id="cove-tooltip"
    ref="tipEl"
    role="tooltip"
    class="cove-tooltip"
    :class="placement"
    :style="{ top: `${top}px`, left: `${left}px` }"
  >{{ text }}</div>
</template>

<script setup lang="ts">
/**
 * One styled tooltip for the whole SPA, in place of the browser's own.
 *
 * Every element's ordinary `title` keeps working as written (no per-component
 * changes): while the pointer is over an element with a title, or keyboard
 * focus is on one, the title is lifted off the element (so the native tooltip,
 * which waits about a second and looks like the OS, doesn't also show) and
 * drawn here instead; it's put back as soon as the pointer or focus leaves.
 * Drawn in a fixed layer at the root, so no card or menu with overflow hidden
 * clips it. Touch is left alone: there is no hover to show it on.
 *
 * Opt an element (and what's inside it) out with `data-no-tooltip`. Iframes
 * never get one (their title is for screen readers).
 */
import { nextTick, onMounted, onUnmounted, ref } from 'vue'

const SHOW_DELAY_MS = 180
const GAP = 8
const EDGE = 6

const tipEl = ref<HTMLElement | null>(null)
const visible = ref(false)
const text = ref('')
const top = ref(0)
const left = ref(0)
const placement = ref<'above' | 'below'>('above')

let anchor: HTMLElement | null = null
let saved = ''
let addedDescribedBy = false
let timer: ReturnType<typeof setTimeout> | null = null
let observer: MutationObserver | null = null

function titled(el: EventTarget | null): HTMLElement | null {
  if (!(el instanceof Element)) return null
  const found = el.closest<HTMLElement>('[title]')
  if (!found || found instanceof HTMLIFrameElement) return null
  if (!(found.getAttribute('title') ?? '').trim()) return null
  if (found.closest('[data-no-tooltip]')) return null
  return found
}

function attach(el: HTMLElement) {
  detach()
  anchor = el
  saved = el.getAttribute('title') ?? ''
  el.removeAttribute('title')
  if (!el.hasAttribute('aria-describedby')) {
    el.setAttribute('aria-describedby', 'cove-tooltip')
    addedDescribedBy = true
  }
  text.value = saved
  // A title that changes while it's showing (a countdown, a status) follows
  // along; Vue writes it back onto the element, so lift it off again.
  observer = new MutationObserver(() => {
    if (!anchor) return
    const next = anchor.getAttribute('title')
    if (next === null) return
    saved = next
    anchor.removeAttribute('title')
    text.value = next
    if (!next.trim()) hide()
    else if (visible.value) nextTick(place)
  })
  observer.observe(el, { attributes: true, attributeFilter: ['title'] })
  timer = setTimeout(show, SHOW_DELAY_MS)
}

function detach() {
  hide()
  observer?.disconnect()
  observer = null
  if (anchor) {
    if (!anchor.hasAttribute('title') && saved) anchor.setAttribute('title', saved)
    if (addedDescribedBy) anchor.removeAttribute('aria-describedby')
  }
  anchor = null
  saved = ''
  addedDescribedBy = false
}

function show() {
  timer = null
  if (!anchor || !anchor.isConnected || !text.value.trim()) return
  visible.value = true
  nextTick(place)
}

/** Hide but stay attached: the pointer is still on the element, and putting
 *  its title back now would bring up the native tooltip after all. */
function hide() {
  if (timer) { clearTimeout(timer); timer = null }
  visible.value = false
}

function place() {
  const tip = tipEl.value
  if (!anchor || !tip) return
  const a = anchor.getBoundingClientRect()
  const t = tip.getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight
  let y = a.top - t.height - GAP
  placement.value = 'above'
  if (y < EDGE) {
    y = Math.min(a.bottom + GAP, vh - t.height - EDGE)
    placement.value = 'below'
  }
  const x = a.left + a.width / 2 - t.width / 2
  left.value = Math.round(Math.max(EDGE, Math.min(x, vw - t.width - EDGE)))
  top.value = Math.round(y)
}

function onPointerOver(e: PointerEvent) {
  if (e.pointerType === 'touch') return
  const target = e.target as Node | null
  const found = titled(e.target)
  // Still inside the current element (its title is lifted, so closest() no
  // longer finds it), unless a titled element nested in it takes over.
  if (anchor && target && anchor.contains(target) && (!found || found.contains(anchor))) return
  if (found) attach(found)
  else if (anchor) detach()
}

function onPointerOut(e: PointerEvent) {
  if (!anchor) return
  const to = e.relatedTarget
  if (to instanceof Node && anchor.contains(to)) return
  detach()
}

function onFocusIn(e: FocusEvent) {
  const el = e.target
  if (!(el instanceof HTMLElement)) return
  // Keyboard focus only: a click focuses too, and shouldn't pop a tooltip.
  let keyboard = false
  try { keyboard = el.matches(':focus-visible') } catch { keyboard = true }
  if (!keyboard) return
  const found = titled(el)
  if (found && found !== anchor) attach(found)
}

function onFocusOut(e: FocusEvent) {
  if (anchor && e.target instanceof Node && anchor.contains(e.target)) detach()
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') hide()
}

const listeners: [string, EventListener, boolean][] = [
  ['pointerover', onPointerOver as EventListener, true],
  ['pointerout', onPointerOut as EventListener, true],
  ['pointerdown', hide as EventListener, true],
  ['focusin', onFocusIn as EventListener, true],
  ['focusout', onFocusOut as EventListener, true],
  ['keydown', onKey as EventListener, true],
  ['scroll', hide as EventListener, true],
]

onMounted(() => {
  for (const [type, fn, capture] of listeners) document.addEventListener(type, fn, capture)
  window.addEventListener('blur', detach)
  window.addEventListener('resize', hide)
})
onUnmounted(() => {
  detach()
  for (const [type, fn, capture] of listeners) document.removeEventListener(type, fn, capture)
  window.removeEventListener('blur', detach)
  window.removeEventListener('resize', hide)
})
</script>

<style scoped>
.cove-tooltip {
  position: fixed;
  z-index: 10000;
  max-width: min(320px, calc(100vw - 12px));
  padding: 5px 9px;
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  box-shadow: var(--glow-sm), var(--shadow);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 0.4px;
  line-height: 1.45;
  text-transform: none;
  text-align: left;
  /* Titles written with \n (an address under its label) keep their lines. */
  white-space: pre-line;
  overflow-wrap: anywhere;
  pointer-events: none;
  animation: tip-in 0.12s ease-out;
}
@keyframes tip-in {
  from { opacity: 0; transform: translateY(2px); }
  to { opacity: 1; transform: none; }
}
.cove-tooltip.below { animation-name: tip-in-below; }
@keyframes tip-in-below {
  from { opacity: 0; transform: translateY(-2px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .cove-tooltip { animation: none; }
}
</style>
