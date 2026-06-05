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

  function toast(message: string, type: Toast['type'] = 'info', duration = 4000) {
    const id = ++_id
    toasts.value.push({ id, message, type })
    setTimeout(() => dismiss(id), duration)
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  // CRT scanline effect — user preference, on by default, persisted.
  const crt = ref(localStorage.getItem('cove_crt') !== 'off')
  watch(
    crt,
    (on) => {
      localStorage.setItem('cove_crt', on ? 'on' : 'off')
      document.body.classList.toggle('cove-crt', on)
    },
    { immediate: true },
  )
  function toggleCrt() {
    crt.value = !crt.value
  }

  return { toasts, toast, dismiss, crt, toggleCrt }
})
