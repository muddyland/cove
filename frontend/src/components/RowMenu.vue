<template>
  <!-- The ⋯ trigger. The menu itself is teleported to <body> with fixed
       positioning so a table's overflow container can never clip it, and it
       flips upward / leftward when it would run off the viewport. -->
  <button
    ref="trigger"
    type="button"
    class="row-menu-trigger"
    :class="{ open }"
    aria-haspopup="menu"
    :aria-expanded="open"
    :aria-label="label"
    :title="label"
    @click.stop="toggle()"
  ><MoreHorizontal :size="16" /></button>
  <Teleport to="body">
    <div
      v-if="open"
      ref="menu"
      class="row-menu"
      role="menu"
      :aria-label="label"
      :style="{ top: `${pos.top}px`, left: `${pos.left}px` }"
      @keydown="onKey"
      @click.stop
      @contextmenu.prevent
    >
      <template v-for="(item, i) in items" :key="item.key">
        <div v-if="item.separator && i > 0" class="row-menu-sep" role="separator" />
        <button
          type="button"
          role="menuitem"
          class="row-menu-item"
          :class="{ danger: item.danger }"
          :disabled="item.disabled"
          :title="item.title"
          @click="choose(item)"
        >
          <component :is="item.icon" v-if="item.icon" :size="14" />
          <span>{{ item.label }}</span>
          <kbd v-if="item.hint" class="row-menu-hint">{{ item.hint }}</kbd>
        </button>
      </template>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, nextTick, onUnmounted, type Component } from 'vue'
import { MoreHorizontal } from 'lucide-vue-next'

export interface RowMenuItem {
  key: string
  label: string
  icon?: Component
  danger?: boolean
  disabled?: boolean
  title?: string
  hint?: string
  // Draw a divider above this item (e.g. to set destructive actions apart).
  separator?: boolean
  action: () => void
}

const props = withDefaults(defineProps<{ items: RowMenuItem[]; label?: string }>(), {
  label: 'More actions',
})

const open = ref(false)
const trigger = ref<HTMLElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const pos = ref({ top: 0, left: 0 })

// Only one row menu open at a time across the page.
let closeOthers: (() => void) | null = null
const OPEN_EVENT = 'cove:row-menu-open'
function onOtherOpened(e: Event) {
  if ((e as CustomEvent).detail !== closeOthers) close(false)
}

async function show(at?: { x: number; y: number }) {
  closeOthers = () => close(false)
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: closeOthers }))
  open.value = true
  window.addEventListener(OPEN_EVENT, onOtherOpened)
  document.addEventListener('click', onOutside, true)
  document.addEventListener('contextmenu', onOutside, true)
  window.addEventListener('resize', onViewportChange)
  window.addEventListener('scroll', onViewportChange, true)
  await nextTick()
  place(at)
  menu.value?.querySelector<HTMLButtonElement>('.row-menu-item:not(:disabled)')?.focus()
}

function place(at?: { x: number; y: number }) {
  const m = menu.value
  if (!m) return
  const w = m.offsetWidth
  const h = m.offsetHeight
  const vw = window.innerWidth
  const vh = window.innerHeight
  let top: number
  let left: number
  if (at) {
    top = at.y
    left = at.x
  } else {
    const r = trigger.value!.getBoundingClientRect()
    top = r.bottom + 4
    left = r.right - w // right-align under the ⋯
    if (top + h > vh - 8) top = r.top - h - 4 // flip up near the bottom
  }
  if (top + h > vh - 8) top = Math.max(8, vh - h - 8)
  if (left + w > vw - 8) left = vw - w - 8
  pos.value = { top: Math.max(8, top), left: Math.max(8, left) }
}

function close(refocus = true) {
  if (!open.value) return
  open.value = false
  window.removeEventListener(OPEN_EVENT, onOtherOpened)
  document.removeEventListener('click', onOutside, true)
  document.removeEventListener('contextmenu', onOutside, true)
  window.removeEventListener('resize', onViewportChange)
  window.removeEventListener('scroll', onViewportChange, true)
  if (refocus) trigger.value?.focus()
}

function toggle(at?: { x: number; y: number }) {
  if (open.value && !at) close()
  else show(at)
}

function onOutside(e: Event) {
  const t = e.target as Node
  if (menu.value?.contains(t) || trigger.value?.contains(t)) return
  close(false)
}
// A scroll or resize would leave a fixed menu floating away from its row.
function onViewportChange(e: Event) {
  if (e.type === 'scroll' && menu.value?.contains(e.target as Node)) return
  close(false)
}

function choose(item: RowMenuItem) {
  if (item.disabled) return
  close()
  item.action()
}

function onKey(e: KeyboardEvent) {
  const buttons = [...(menu.value?.querySelectorAll<HTMLButtonElement>('.row-menu-item:not(:disabled)') ?? [])]
  const idx = buttons.indexOf(document.activeElement as HTMLButtonElement)
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    buttons[(idx + 1) % buttons.length]?.focus()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    buttons[(idx - 1 + buttons.length) % buttons.length]?.focus()
  } else if (e.key === 'Home') {
    e.preventDefault()
    buttons[0]?.focus()
  } else if (e.key === 'End') {
    e.preventDefault()
    buttons[buttons.length - 1]?.focus()
  } else if (e.key === 'Tab') {
    close(false)
  }
}

onUnmounted(() => close(false))

// Parents call this from a row's @contextmenu to open the menu at the cursor.
defineExpose({
  openAt: (e: MouseEvent) => {
    if (!props.items.length) return
    e.preventDefault()
    show({ x: e.clientX, y: e.clientY })
  },
  close,
})
</script>

<style scoped>
.row-menu-trigger {
  display: inline-flex; align-items: center; justify-content: center;
  width: 30px; height: 28px; padding: 0;
  background: none; border: 1px solid transparent; border-radius: var(--radius-sm);
  color: var(--text-muted); cursor: pointer; transition: all 0.15s;
  vertical-align: middle;
}
.row-menu-trigger:hover, .row-menu-trigger.open {
  color: var(--accent); border-color: var(--border); background: var(--surface-2);
}
</style>

<!-- Unscoped: the menu is teleported out of this component's subtree. -->
<style>
.row-menu {
  position: fixed; z-index: 1000;
  display: flex; flex-direction: column; gap: 2px;
  min-width: 200px; max-width: min(320px, calc(100vw - 16px));
  max-height: calc(100vh - 16px); overflow-y: auto;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
  box-shadow: var(--glow-sm), var(--shadow); padding: 5px; text-align: left;
}
.row-menu-item {
  display: flex; align-items: center; gap: 9px; width: 100%;
  padding: 8px 10px; background: none; border: none; border-radius: var(--radius-sm);
  color: var(--text); font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.5px;
  text-align: left; cursor: pointer; white-space: nowrap;
}
.row-menu-item svg { flex-shrink: 0; color: var(--text-muted); }
.row-menu-item:hover:not(:disabled), .row-menu-item:focus-visible { background: var(--accent-dim); outline: none; }
.row-menu-item:hover:not(:disabled) svg, .row-menu-item:focus-visible svg { color: var(--accent); }
.row-menu-item:disabled { opacity: 0.45; cursor: not-allowed; }
.row-menu-item.danger, .row-menu-item.danger svg { color: var(--red); }
.row-menu-item.danger:hover:not(:disabled), .row-menu-item.danger:focus-visible {
  background: color-mix(in srgb, var(--red) 14%, transparent);
}
.row-menu-sep { height: 1px; background: var(--border); margin: 3px 4px; }
.row-menu-hint {
  margin-left: auto; padding-left: 12px;
  font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);
}
</style>
