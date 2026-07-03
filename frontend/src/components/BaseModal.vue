<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="modelValue" class="overlay" @mousedown.self="close">
        <div
          ref="modalEl"
          class="modal"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
          tabindex="-1"
          :style="{ width: width || '480px' }"
          @keydown.esc.stop.prevent="close"
          @keydown.tab="onTab"
        >
          <div class="modal-header">
            <span class="modal-title">{{ title }}</span>
            <button class="close-btn" @click="close" aria-label="Close"><X :size="16" /></button>
          </div>
          <div class="modal-body">
            <slot />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { X } from 'lucide-vue-next'

const props = defineProps<{ modelValue: boolean; title: string; width?: string }>()
const emit = defineEmits(['update:modelValue'])

const modalEl = ref<HTMLElement | null>(null)
let lastFocused: HTMLElement | null = null

function close() {
  emit('update:modelValue', false)
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusableEls(): HTMLElement[] {
  if (!modalEl.value) return []
  // Only visible controls participate in the focus trap (offsetParent is null for
  // display:none elements — e.g. fields hidden behind a v-if).
  return Array.from(modalEl.value.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    el => el.offsetParent !== null,
  )
}

// Keep Tab (and Shift-Tab) cycling within the dialog instead of escaping to the
// page behind the scrim.
function onTab(e: KeyboardEvent) {
  const els = focusableEls()
  if (!els.length) {
    e.preventDefault()
    modalEl.value?.focus()
    return
  }
  const first = els[0]
  const last = els[els.length - 1]
  const active = document.activeElement as HTMLElement | null
  const inside = active && modalEl.value?.contains(active)
  if (e.shiftKey && (active === first || !inside)) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && (active === last || !inside)) {
    e.preventDefault()
    first.focus()
  }
}

watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      lastFocused = document.activeElement as HTMLElement | null
      await nextTick()
      // Prefer an element that opts in via [data-autofocus] (e.g. ConfirmModal's
      // primary button); otherwise the first real field, skipping the ✕ button so
      // focus lands on content, not the dismiss control.
      const preferred = modalEl.value?.querySelector<HTMLElement>('[data-autofocus]')
      const els = focusableEls().filter(el => !el.classList.contains('close-btn'))
      ;(preferred ?? els[0] ?? modalEl.value)?.focus()
    } else {
      // Return focus to whatever opened the modal.
      lastFocused?.focus?.()
      lastFocused = null
    }
  },
)
</script>

<style scoped>
.overlay {
  position: fixed; inset: 0;
  /* Solid scrim only — no backdrop-filter. A full-viewport backdrop blur keeps a
     GPU compositing layer alive for as long as any modal is mounted, which can
     drop the browser's hardware video-overlay path and make video stutter in
     other windows/screens. The 0.8 black already separates the modal cleanly. */
  background: rgba(0, 0, 0, 0.8);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal {
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  box-shadow: var(--glow), var(--shadow);
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 64px);
  display: flex; flex-direction: column;
}
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  position: relative;
}
.modal-header::after {
  content: '';
  position: absolute;
  bottom: -1px; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0.5;
}
.modal-title {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--accent);
  text-shadow: var(--glow-sm);
}
.close-btn {
  background: none; border: none; color: var(--text-muted);
  cursor: pointer; font-size: 12px; font-family: var(--font-mono);
  letter-spacing: 1px; padding: 2px 6px; transition: color 0.15s;
  display: inline-flex; align-items: center;
}
.close-btn:hover { color: var(--red); text-shadow: 0 0 8px var(--red); }
.modal-body { padding: 20px; overflow-y: auto; }

.modal-enter-active, .modal-leave-active { transition: all 0.2s; }
.modal-enter-from, .modal-leave-to { opacity: 0; transform: scale(0.95) translateY(-12px); }
</style>
