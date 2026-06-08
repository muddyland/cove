import { ref } from 'vue'
import { registerSW } from 'virtual:pwa-register'

// True once a new service worker is built and waiting — the UI shows a
// "Reload" pill (see UpdateBanner.vue) so the user applies it when convenient.
export const needRefresh = ref(false)

// Captured Chrome/Android install prompt. The browser fires this when the
// current page's manifest is installable; we stash it so an in-app "Install"
// button can trigger the native prompt on demand. Null on iOS/Safari (which has
// no programmatic prompt — install is via Share → Add to Home Screen).
export const installPrompt = ref<any>(null)

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e: any) => {
    // Keep the event so we can call .prompt() later from a user gesture.
    e.preventDefault()
    installPrompt.value = e
  })
  window.addEventListener('appinstalled', () => {
    installPrompt.value = null
  })
}

// True when running as an installed/standalone PWA (so we can hide the install
// affordance). Covers both the standard display-mode and iOS's navigator.standalone.
export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false
  return (
    window.matchMedia?.('(display-mode: standalone)').matches ||
    (window.navigator as any).standalone === true
  )
}

// Trigger the native install prompt. Returns 'accepted' | 'dismissed' when a
// prompt was shown, or 'unavailable' when the browser has none (e.g. iOS).
export async function promptInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  const e = installPrompt.value
  if (!e) return 'unavailable'
  e.prompt()
  try {
    const choice = await e.userChoice
    return choice?.outcome === 'accepted' ? 'accepted' : 'dismissed'
  } finally {
    installPrompt.value = null
  }
}

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
