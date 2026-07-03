import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export interface Toast {
  id: number
  message: string
  type: 'success' | 'error' | 'info'
}

export const useUiStore = defineStore('ui', () => {
  const toasts = ref<Toast[]>([])
  let _id = 0

  // Errors persist until dismissed (duration 0) so a glance away can't lose the
  // only report that something failed; success/info auto-dismiss. An explicit
  // duration always wins.
  function toast(
    message: string,
    type: Toast['type'] = 'info',
    duration = type === 'error' ? 0 : 4000,
  ) {
    const id = ++_id
    toasts.value.push({ id, message, type })
    if (duration > 0) setTimeout(() => dismiss(id), duration)
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  // CRT scanline effect — user preference, on by default, persisted. Applied to
  // the workspace stream iframe (the connect page), not globally.
  const crt = ref(localStorage.getItem('cove_crt') !== 'off')
  watch(crt, (on) => {
    localStorage.setItem('cove_crt', on ? 'on' : 'off')
  })
  function toggleCrt() {
    crt.value = !crt.value
  }

  return { toasts, toast, dismiss, crt, toggleCrt }
})
