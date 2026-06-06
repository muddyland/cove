import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useUiStore } from '@/stores/ui'

describe('ui store', () => {
  beforeEach(() => {
    localStorage.clear()
    document.body.className = ''
    vi.useFakeTimers()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('toasts', () => {
    it('toast() adds a toast', () => {
      const ui = useUiStore()
      expect(ui.toasts).toHaveLength(0)
      ui.toast('hello', 'success')
      expect(ui.toasts).toHaveLength(1)
      expect(ui.toasts[0]).toMatchObject({ message: 'hello', type: 'success' })
      expect(typeof ui.toasts[0].id).toBe('number')
    })

    it('defaults toast type to info', () => {
      const ui = useUiStore()
      ui.toast('plain')
      expect(ui.toasts[0].type).toBe('info')
    })

    it('dismiss() removes a toast by id', () => {
      const ui = useUiStore()
      ui.toast('one')
      ui.toast('two')
      const firstId = ui.toasts[0].id
      ui.dismiss(firstId)
      expect(ui.toasts).toHaveLength(1)
      expect(ui.toasts[0].message).toBe('two')
    })

    it('auto-dismisses after the duration', () => {
      const ui = useUiStore()
      ui.toast('temp', 'info', 4000)
      expect(ui.toasts).toHaveLength(1)
      vi.advanceTimersByTime(4000)
      expect(ui.toasts).toHaveLength(0)
    })
  })

  describe('crt', () => {
    it('defaults to on when localStorage is empty', () => {
      const ui = useUiStore()
      expect(ui.crt).toBe(true)
    })

    it('defaults to off when localStorage says off', () => {
      localStorage.setItem('cove_crt', 'off')
      const ui = useUiStore()
      expect(ui.crt).toBe(false)
    })

    it('toggleCrt() flips the value and persists the preference', async () => {
      const ui = useUiStore()
      expect(ui.crt).toBe(true)

      ui.toggleCrt()
      // watcher runs on next tick; flush microtasks
      await Promise.resolve()
      expect(ui.crt).toBe(false)
      expect(localStorage.getItem('cove_crt')).toBe('off')

      ui.toggleCrt()
      await Promise.resolve()
      expect(ui.crt).toBe(true)
      expect(localStorage.getItem('cove_crt')).toBe('on')
    })
  })
})
