import { ref } from 'vue'
import { registerSW } from 'virtual:pwa-register'

// True once a new service worker is built and waiting — the UI shows a
// "Reload" pill (see UpdateBanner.vue) so the user applies it when convenient.
export const needRefresh = ref(false)

let updateSW: ((reloadPage?: boolean) => Promise<void>) | undefined

export function initPwa(): void {
  updateSW = registerSW({
    immediate: true,
    onNeedRefresh() {
      needRefresh.value = true
    },
    onRegisteredSW(_swUrl, registration) {
      if (!registration) return
      const check = () => {
        registration.update().catch(() => {})
      }
      // A standalone iOS PWA only checks for a new service worker at launch, so
      // it appears "stuck" until a full close/reopen. Force a check periodically
      // and whenever the app returns to the foreground (the common case: you
      // reopen the backgrounded PWA after a deploy).
      setInterval(check, 30 * 60 * 1000)
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') check()
      })
    },
  })
}

export function applyUpdate(): void {
  needRefresh.value = false
  void updateSW?.(true) // skipWaiting + reload to the new version
}
