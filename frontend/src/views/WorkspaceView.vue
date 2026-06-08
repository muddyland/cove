<template>
  <div class="workspace-page">
    <div class="top-bar">
      <RouterLink v-if="!lockedToWorkspace" to="/app" class="back-link"><span aria-hidden="true">←</span><span class="bl-label"> GRID</span></RouterLink>
      <div class="ws-info">
        <!-- Locked to this one node when launched as its own installed app: a
             per-workspace PWA is single-purpose, so no cross-node switching. -->
        <div v-if="lockedToWorkspace" class="ws-switcher">
          <span class="ws-name ws-name-locked">
            <img v-if="ws" class="ws-icon" :src="ws.image_logo || '/favicon.svg'" alt="" />
            {{ ws?.name }}
          </span>
        </div>
        <div v-else class="ws-switcher">
          <button class="ws-switch-btn" :class="{ open: menuOpen }" @click.stop="menuOpen = !menuOpen">
            <img v-if="ws" class="ws-icon" :src="ws.image_logo || '/favicon.svg'" alt="" />
            <span class="ws-name">{{ ws?.name }}</span>
            <ChevronDown :size="14" class="chev" />
          </button>
          <div v-if="menuOpen" class="switch-menu" @click.stop>
            <button
              v-for="w in switchable"
              :key="w.id"
              type="button"
              class="switch-item"
              :class="{ current: w.id === wsId }"
              @click="selectNode(w)"
            >
              <span class="switch-dot" :class="w.status" />
              <span class="switch-name">{{ w.name }}</span>
              <span class="switch-img">{{ w.image_name }}</span>
              <Power v-if="w.status !== 'running'" :size="12" class="switch-boot" />
            </button>
            <p v-if="!switchable.length" class="switch-empty">No nodes</p>
          </div>
        </div>
        <StatusBadge v-if="ws" :status="ws.status" class="ws-status" />
        <!-- Connection indicators: a running node with a routing flag means its
             sidecar is healthy (a failed one would force the node into error). -->
        <span
          v-if="ws?.status === 'running' && ws.use_gluetun"
          class="conn-icon vpn-on"
          title="VPN connected"
        ><Lock :size="14" /></span>
        <span
          v-if="ws?.status === 'running' && ws.use_tailscale"
          class="conn-icon ts-on"
          :title="ws.ts_exit_node ? `Tailscale connected · exit node: ${ws.ts_exit_node}` : 'Tailscale connected'"
        ><Network :size="14" /></span>
      </div>
      <RouterLink v-if="!lockedToWorkspace" to="/app" class="brand-center" title="Back to grid">
        <img src="/favicon.svg" alt="" />
        <span>COVE</span>
      </RouterLink>
      <div class="top-actions">
        <button
          v-if="ws && !standalone"
          class="bar-btn install-btn"
          title="Install this workspace as an app"
          @click="handleInstall"
        ><Download :size="14" /><span class="bar-label"> APP</span></button>
        <button
          v-if="ws?.status === 'running'"
          class="bar-btn crt-btn"
          :class="{ active: ui.crt }"
          :title="ui.crt ? 'CRT effect on' : 'CRT effect off'"
          @click="ui.toggleCrt()"
        ><ScanLine :size="14" /><span class="bar-label"> CRT</span></button>
        <button
          v-if="ws?.status === 'running'"
          class="bar-btn fs-btn"
          :title="isFullscreen ? 'Exit fullscreen' : 'Fullscreen'"
          @click="toggleFullscreen"
        ><component :is="isFullscreen ? Minimize : Maximize" :size="14" /><span class="bar-label"> {{ isFullscreen ? 'WINDOW' : 'FULL' }}</span></button>
        <NeonButton v-if="ws?.status === 'running'" variant="warn" :loading="stopping" @click="handleStop"><Square :size="14" /><span class="bar-label"> HALT</span></NeonButton>
      </div>
    </div>

    <!-- Halted takeover for a per-workspace PWA: it's a single-purpose app, so on
         halt there's nowhere to go back to — just confirm it's safe to close. -->
    <div v-if="showHalted" class="overlay-state halted">
      <PowerOff class="halt-icon" :size="56" />
      <p class="boot-text">WORKSPACE HALTED</p>
      <p class="boot-sub">{{ ws?.name ? `“${ws.name}” has stopped.` : 'The workspace has stopped.' }} It’s safe to close this app now.</p>
    </div>

    <div v-else-if="isBooting" class="overlay-state">
      <img class="boot-icon" src="/favicon.svg" alt="" />
      <p class="boot-text">{{ installing ? 'PROVISIONING NODE' : 'BOOTING NODE' }}<span class="ellipsis" /></p>
      <p v-if="installing" class="boot-sub">Installing packages &amp; proot-apps — this can take a few minutes.</p>
      <p v-else class="boot-sub">Waiting for the container to start…</p>
    </div>

    <div class="frame-wrap" v-else-if="ws?.status === 'running' && streamUrl" ref="frameWrap">
      <iframe
        :src="streamUrl"
        class="workspace-frame"
        allow="autoplay; clipboard-read; clipboard-write; fullscreen; camera; microphone"
        allowfullscreen
      />
      <!-- CRT scanline / flicker overlay (pointer-events:none so the stream stays
           interactive). Toggled per-user from the top bar. -->
      <div v-if="ui.crt" class="crt-overlay" aria-hidden="true" />
    </div>

    <div v-else-if="ws?.status === 'error'" class="overlay-state error">
      <p class="error-text">⚠ {{ ws.error_message || 'Node failed to boot' }}</p>
      <NeonButton variant="secondary" @click="$router.push('/app')">← RETURN TO GRID</NeonButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { workspacesApi } from '@/api/workspaces'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import StatusBadge from '@/components/StatusBadge.vue'
import NeonButton from '@/components/NeonButton.vue'
import { promptInstall, isStandalone } from '@/pwa'
import { ScanLine, Maximize, Minimize, ChevronDown, Power, PowerOff, Square, Download, Lock, Network } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const store = useWorkspacesStore()
const ui = useUiStore()

const wsId = computed(() => Number(route.params.id))
const ws = computed(() => store.items.find(w => w.id === wsId.value))
const installing = computed(() => !!(ws.value?.install_packages || ws.value?.proot_apps))
const stopping = ref(false)
const streamUrl = ref<string | null>(null)
const frameWrap = ref<HTMLElement | null>(null)
const isFullscreen = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

// Quick-switch dropdown: jump between nodes (and boot stopped ones) without
// going via the grid. Running nodes first, then booting, then the rest.
const menuOpen = ref(false)
const switchable = computed(() => {
  const rank = (s: string) => (s === 'running' ? 0 : s === 'creating' || s === 'stopping' ? 1 : 2)
  return [...store.items].sort(
    (a, b) => rank(a.status) - rank(b.status) || a.name.localeCompare(b.name),
  )
})
function closeMenu() { menuOpen.value = false }

async function selectNode(w: { id: number; name: string; status: string }) {
  menuOpen.value = false
  if (w.id === wsId.value) return  // already viewing it
  if (w.status !== 'running') {
    try {
      await store.start(w.id)
      ui.toast(`Booting ${w.name}…`, 'info')
    } catch (e: any) {
      ui.toast(e.message || 'Failed to boot', 'error')
      return
    }
  }
  // Stay in the current context: inside the dashboard app keep the /app prefix;
  // from a standalone per-workspace entry switch to the sibling node's entry.
  router.push(`${inAppRoute.value ? '/app' : ''}/workspace/${w.id}`)
}
watch(menuOpen, (open) => {
  if (open) document.addEventListener('click', closeMenu)
  else document.removeEventListener('click', closeMenu)
})
// The router reuses this component instance across /workspace/:id changes, so
// onMounted doesn't re-run. Re-initialise for the new node: drop the previous
// node's stream URL (forcing the iframe to reload), restart polling if it's
// booting, and mint a fresh stream URL if it's already running.
watch(wsId, async () => {
  menuOpen.value = false
  streamUrl.value = null
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (!ws.value) await store.fetch()
  startPollIfNeeded()
  await loadStreamUrl()
})

// Fullscreen the whole frame wrapper (iframe + CRT overlay + branding), not
// just the iframe, so the overlay stays in sync.
function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen?.()
  } else {
    frameWrap.value?.requestFullscreen?.().catch(() => {})
  }
}
function onFullscreenChange() {
  isFullscreen.value = document.fullscreenElement === frameWrap.value
}

async function loadStreamUrl() {
  // Mint the authenticated iframe URL. In subdomain mode this carries a one-time
  // token that bootstraps a per-workspace stream cookie; in subpath mode it is
  // the plain same-origin path. Never reuse the SPA session cookie cross-origin.
  if (ws.value?.status !== 'running' || streamUrl.value) return
  try {
    const { url } = await workspacesApi.streamAuth(wsId.value)
    streamUrl.value = url
  } catch (e: any) {
    ui.toast(e.message || 'Failed to open stream', 'error')
  }
}

onMounted(async () => {
  document.addEventListener('fullscreenchange', onFullscreenChange)
  if (!ws.value) await store.fetch()
  await ensureRunningIfLocked()
  startPollIfNeeded()
  await loadStreamUrl()
})
onUnmounted(() => {
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.removeEventListener('click', closeMenu)
  if (pollTimer) clearInterval(pollTimer)
})

watch(() => ws.value?.status, (s) => {
  if (s === 'running' || s === 'error') {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    booting.value = false
  }
  if (s === 'running') loadStreamUrl()
})

function startPollIfNeeded() {
  if (ws.value?.status === 'creating' && !pollTimer) {
    pollTimer = setInterval(async () => {
      const fresh = await workspacesApi.get(wsId.value)
      const idx = store.items.findIndex(w => w.id === wsId.value)
      if (idx !== -1) store.items[idx] = fresh
      else store.items.push(fresh)
      if (fresh.status !== 'creating') { clearInterval(pollTimer!); pollTimer = null }
    }, 2000)
  }
}

// --- Per-workspace PWA identity ---------------------------------------------
// While viewing a workspace, swap the app's manifest/title/icon to that node's
// so the browser's "Install" / "Add to Home Screen" creates an app that looks
// like the workspace (e.g. a Brave icon named "Brave") and launches straight
// into it. Restored to the Cove defaults when leaving the page.
const standalone = ref(isStandalone())

// True when viewing inside the dashboard app (/app/workspace/:id) rather than at
// the standalone per-workspace entry (/workspace/:id). Only the standalone entry
// carries the per-workspace PWA identity + is installable as its own app.
const inAppRoute = computed(() => route.path.startsWith('/app/'))

// A standalone per-workspace app (its own icon, launched at /workspace/:id) is
// locked to this single node — no switcher. The dashboard PWA (/app/...) keeps
// switching, since browsing across nodes is its whole purpose.
const lockedToWorkspace = computed(() => standalone.value && !inAppRoute.value)

// Set when the user halts from inside the per-workspace PWA: there's nowhere to
// go back to, so we show a "safe to close" takeover instead of navigating.
const halted = ref(false)
// Set while auto-booting an offline node on PWA launch.
const booting = ref(false)
const showHalted = computed(() => lockedToWorkspace.value && halted.value)
const isBooting = computed(
  () => !showHalted.value && (booting.value || !ws.value || ws.value.status === 'creating'),
)

// A per-workspace PWA opened while its node is offline boots it automatically and
// shows the booting screen — the app is for that one node, so there's nothing to
// do but bring it up. Not in the dashboard/browser, where the user starts nodes
// explicitly from the grid.
async function ensureRunningIfLocked() {
  if (!lockedToWorkspace.value || halted.value) return
  if (ws.value?.status !== 'stopped') return
  booting.value = true
  try {
    await store.start(wsId.value)
  } catch (e: any) {
    ui.toast(e.message || 'Failed to start workspace', 'error')
    booting.value = false
    return
  }
  startPollIfNeeded()
}

function isIos(): boolean {
  const ua = navigator.userAgent || ''
  return (
    /iP(hone|ad|od)/.test(ua) ||
    // iPadOS reports as desktop Safari but has touch points.
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  )
}

let savedManifestHref: string | null = null
let savedManifestCrossOrigin: string | null = null
let savedTitle: string | null = null
let savedAppleTitle: string | null = null
let savedAppleIcon: string | null = null

function headLink(rel: string): HTMLLinkElement {
  let el = document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`)
  if (!el) {
    el = document.createElement('link')
    el.rel = rel
    document.head.appendChild(el)
  }
  return el
}
function headMeta(name: string): HTMLMetaElement {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[name="${name}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.name = name
    document.head.appendChild(el)
  }
  return el
}

function applyAppIdentity(w: { id: number; name: string; image_logo?: string | null }) {
  const manifest = headLink('manifest')
  if (savedManifestHref === null) {
    savedManifestHref = manifest.getAttribute('href') ?? ''
    savedManifestCrossOrigin = manifest.getAttribute('crossorigin')
  }
  // use-credentials so the browser sends the session cookie when fetching this
  // authenticated, same-origin manifest.
  manifest.setAttribute('crossorigin', 'use-credentials')
  manifest.setAttribute('href', `/api/workspaces/${w.id}/manifest.webmanifest`)

  if (savedTitle === null) savedTitle = document.title
  document.title = w.name

  const appleTitle = headMeta('apple-mobile-web-app-title')
  if (savedAppleTitle === null) savedAppleTitle = appleTitle.getAttribute('content')
  appleTitle.setAttribute('content', w.name)

  // iOS uses apple-touch-icon (not manifest icons) for the home-screen icon.
  const appleIcon = headLink('apple-touch-icon')
  if (savedAppleIcon === null) savedAppleIcon = appleIcon.getAttribute('href')
  appleIcon.setAttribute('href', w.image_logo || '/apple-touch-icon.png')
}

function restoreAppIdentity() {
  const manifest = document.head.querySelector<HTMLLinkElement>('link[rel="manifest"]')
  if (manifest && savedManifestHref !== null) {
    manifest.setAttribute('href', savedManifestHref)
    if (savedManifestCrossOrigin === null) manifest.removeAttribute('crossorigin')
    else manifest.setAttribute('crossorigin', savedManifestCrossOrigin)
  }
  if (savedTitle !== null) document.title = savedTitle
  if (savedAppleTitle !== null) {
    const el = document.head.querySelector<HTMLMetaElement>('meta[name="apple-mobile-web-app-title"]')
    if (el) el.setAttribute('content', savedAppleTitle)
  }
  if (savedAppleIcon !== null) {
    const el = document.head.querySelector<HTMLLinkElement>('link[rel="apple-touch-icon"]')
    if (el) el.setAttribute('href', savedAppleIcon)
  }
  savedManifestHref = savedTitle = savedAppleTitle = savedAppleIcon = null
  savedManifestCrossOrigin = null
}

// Swap in the per-workspace identity only on the standalone entry; inside the
// dashboard app keep the Cove identity (and don't make /app/workspace claim the
// workspace manifest).
function syncAppIdentity() {
  if (ws.value && !inAppRoute.value) applyAppIdentity(ws.value)
  else restoreAppIdentity()
}
watch(
  () => [ws.value?.id, ws.value?.name, ws.value?.image_logo, inAppRoute.value] as const,
  syncAppIdentity,
  { immediate: true },
)
onUnmounted(restoreAppIdentity)

async function handleInstall() {
  // Inside the dashboard app the page is within its /app scope, so the browser
  // suppresses installing a second app here. Hop to the standalone entry (its own
  // scope) where the per-workspace app is installable.
  if (inAppRoute.value) {
    window.location.assign(`/workspace/${wsId.value}`)
    return
  }
  const outcome = await promptInstall()
  if (outcome === 'accepted') {
    standalone.value = true
    ui.toast(`Installed ${ws.value?.name ?? 'app'}`, 'success')
    return
  }
  if (outcome === 'dismissed') return
  // 'unavailable' — the browser gave us no install prompt. Why depends on platform.
  if (isIos()) {
    if (isStandalone()) {
      // Inside the installed app there's no Share menu; bounce to Safari where
      // "Add to Home Screen" is available.
      window.open(window.location.href, '_blank')
      ui.toast('Opened in your browser — use Share → Add to Home Screen to install', 'info', 7000)
    } else {
      ui.toast('Tap the Share button, then "Add to Home Screen" to install this app', 'info', 7000)
    }
    return
  }
  // Desktop / Android Chromium (incl. Brave): no programmatic prompt means it's
  // either already installed or the browser is offering it elsewhere. Point at the
  // address-bar install icon.
  ui.toast(
    'Use the install icon in your browser’s address bar to install this workspace as an app.',
    'info',
    7000,
  )
}

async function handleStop() {
  stopping.value = true
  try {
    await store.stop(wsId.value)
    if (lockedToWorkspace.value) {
      // Per-workspace PWA: no grid to return to — show the "safe to close" state.
      halted.value = true
    } else {
      router.push('/app')
    }
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    stopping.value = false
  }
}
</script>

<style scoped>
/* Use the dynamic viewport unit so mobile browser chrome (the collapsing URL
   bar) can't push the bottom of the stream out of view; 100vh is the fallback
   for browsers without dvh. overscroll-behavior:none kills iOS rubber-banding. */
.workspace-page {
  display: flex; flex-direction: column;
  height: 100vh;
  height: 100dvh;
  background: #000; overflow: hidden;
  overscroll-behavior: none;
}

.top-bar {
  display: flex; align-items: center; gap: 16px;
  padding: 0 16px;
  /* Sit clear of the iOS notch / status bar (black-translucent makes the page
     draw underneath it). The inset is 0 on devices without a notch. */
  padding-left: max(16px, env(safe-area-inset-left));
  padding-right: max(16px, env(safe-area-inset-right));
  padding-top: env(safe-area-inset-top);
  box-sizing: content-box;
  height: 44px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}
.top-bar::after {
  content: '';
  position: absolute; bottom: -1px; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0.5;
}

.back-link {
  display: inline-flex; align-items: center;
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px;
  color: var(--accent); text-decoration: none; flex-shrink: 0;
  padding: 4px 10px;
  border: 1px solid rgba(0, 245, 255, 0.4);
  border-radius: var(--radius-sm);
  text-shadow: var(--glow-sm);
  transition: all 0.15s;
}
.back-link:hover {
  color: #fff;
  border-color: var(--accent);
  background: var(--accent-dim);
  box-shadow: var(--glow-sm);
}

.ws-info { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; }
.ws-name { font-family: var(--font-mono); font-size: 12px; letter-spacing: 1px; }
.ws-name-locked { display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; color: var(--text); }
.ws-icon { width: 16px; height: 16px; border-radius: 3px; object-fit: contain; flex-shrink: 0; }
.conn-icon { display: inline-flex; align-items: center; flex-shrink: 0; }
.conn-icon.vpn-on { color: var(--green); filter: drop-shadow(0 0 4px rgba(0, 255, 157, 0.5)); }
.conn-icon.ts-on { color: var(--accent-2); filter: drop-shadow(0 0 4px rgba(255, 0, 170, 0.5)); }

/* Quick-switch dropdown */
.ws-switcher { position: relative; }
.ws-switch-btn {
  display: inline-flex; align-items: center; gap: 6px;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 3px 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.ws-switch-btn:hover, .ws-switch-btn.open {
  border-color: var(--accent);
  background: var(--accent-dim);
}
.ws-switch-btn .chev { color: var(--accent); transition: transform 0.15s; flex-shrink: 0; }
.ws-switch-btn.open .chev { transform: rotate(180deg); }

.switch-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  min-width: 240px;
  max-width: calc(100vw - 24px);
  max-height: 60vh;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  box-shadow: var(--glow-sm), var(--shadow);
  padding: 4px;
  z-index: 30;
}
.switch-item {
  display: flex; align-items: center; gap: 8px;
  width: 100%;
  padding: 7px 8px;
  border: none;
  background: none;
  border-radius: var(--radius-sm);
  text-align: left;
  color: var(--text);
  cursor: pointer;
  transition: background 0.12s;
}
.switch-item:hover { background: var(--accent-dim); }
.switch-item.current { background: rgba(0, 245, 255, 0.06); }
.switch-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
  background: var(--text-muted);
}
.switch-dot.running { background: var(--green); box-shadow: 0 0 6px var(--green); }
.switch-dot.creating, .switch-dot.stopping { background: var(--amber); box-shadow: 0 0 6px var(--amber); }
.switch-dot.error { background: var(--red); box-shadow: 0 0 6px var(--red); }
.switch-boot { color: var(--green); flex-shrink: 0; }
.switch-name {
  font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.switch-item.current .switch-name { color: var(--accent); }
.switch-img {
  margin-left: auto;
  font-family: var(--font-mono); font-size: 10px; color: var(--text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 50%;
}
.switch-empty {
  font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);
  padding: 8px; text-align: center;
}
.top-actions { margin-left: auto; display: flex; align-items: center; gap: 10px; flex-shrink: 0; }

.frame-wrap { flex: 1; position: relative; overflow: hidden; min-height: 0; }
.workspace-frame { width: 100%; height: 100%; border: none; display: block; }

/* CRT effect over the stream: fine scanlines + a soft vignette + a faint
   flicker. pointer-events:none keeps the underlying iframe fully interactive. */
.crt-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  /* Clip the rolling ::after band — its translateY would otherwise extend past
     the frame and inflate the page's scrollable height (a growing scrollbar that
     resets each animation loop, very visible in Edge). */
  overflow: hidden;
  z-index: 5;
  background:
    radial-gradient(ellipse at center, transparent 60%, rgba(0, 0, 0, 0.35) 100%),
    repeating-linear-gradient(
      0deg,
      rgba(0, 0, 0, 0.18),
      rgba(0, 0, 0, 0.18) 1px,
      transparent 1px,
      transparent 3px
    );
  mix-blend-mode: multiply;
  animation: crt-flicker 6s steps(60) infinite;
}
/* A second, very subtle moving scanline band for the rolling-CRT feel. */
.crt-overlay::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(rgba(0, 245, 255, 0.04), transparent 40%);
  height: 30%;
  animation: crt-roll 8s linear infinite;
}
@keyframes crt-flicker {
  0%, 100% { opacity: 0.85; }
  48% { opacity: 0.9; }
  50% { opacity: 0.78; }
  52% { opacity: 0.9; }
}
@keyframes crt-roll {
  0% { transform: translateY(-100%); }
  100% { transform: translateY(430%); }
}

/* Shared base for the small top-bar toggle buttons (CRT, fullscreen). */
.bar-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 10px;
  font-family: var(--font-mono);
  letter-spacing: 1px;
  padding: 4px 8px;
  cursor: pointer;
  transition: all 0.15s;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

/* CRT — magenta/retro (distinct from the amber HALT). Outlined off, filled on. */
.crt-btn {
  color: var(--accent-2);
  border-color: rgba(255, 0, 170, 0.45);
}
.crt-btn:hover {
  color: #fff;
  border-color: var(--accent-2);
  box-shadow: 0 0 8px rgba(255, 0, 170, 0.5);
}
.crt-btn.active {
  color: #fff;
  background: var(--accent-2);
  border-color: var(--accent-2);
  box-shadow: 0 0 10px rgba(255, 0, 170, 0.6);
}

/* Install-as-app — cyan (matches the brand accent). */
.install-btn {
  color: var(--accent);
  border-color: rgba(0, 245, 255, 0.45);
}
.install-btn:hover {
  color: #fff;
  border-color: var(--accent);
  box-shadow: 0 0 8px rgba(0, 245, 255, 0.5);
}

/* Fullscreen — green. */
.fs-btn {
  color: var(--green);
  border-color: rgba(0, 255, 157, 0.45);
}
.fs-btn:hover {
  color: #fff;
  border-color: var(--green);
  box-shadow: 0 0 8px rgba(0, 255, 157, 0.5);
}

/* COVE logo centered in the top bar. Absolutely positioned so the back-link,
   switcher, and actions keep their natural left/right flow. */
.brand-center {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--accent);
  text-decoration: none;
  opacity: 0.85;
  transition: opacity 0.2s;
}
.brand-center:hover { opacity: 1; }
.brand-center img { width: 20px; height: 20px; }
.brand-center span {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 4px;
  text-shadow: var(--glow-sm);
}
/* Hide the centered logo on narrow screens so it can't overlap the controls. */
@media (max-width: 700px) {
  .brand-center { display: none; }
}

/* Phone layout: the top bar is the tightest spot — back-link + switcher + status
   + CRT/FULL/HALT must fit ~360px. Collapse text labels to icons, tighten gaps,
   and let the switcher name (the only flexible item) absorb the remaining width. */
@media (max-width: 560px) {
  .top-bar { gap: 8px; padding-left: max(10px, env(safe-area-inset-left)); padding-right: max(10px, env(safe-area-inset-right)); }
  .back-link { padding: 4px 8px; }
  .back-link .bl-label { display: none; }      /* show just the ← arrow */
  .bar-label { display: none; }                 /* CRT / FULL / HALT become icon-only */
  .bar-btn { padding: 6px 8px; }                /* keep a comfortable tap target */
  .top-actions { gap: 6px; }
  /* The ONLINE badge is redundant on the stream (you're connected) and is the
     main thing crowding the actions — drop it so the buttons get their space. */
  .ws-status { display: none; }
  .ws-switch-btn .ws-name { max-width: 32vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}

.overlay-state {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 20px;
}
.boot-icon {
  width: 64px; height: 64px;
  animation: bootpulse 1.6s ease-in-out infinite;
}
@keyframes bootpulse {
  0%, 100% { opacity: 0.5; transform: scale(0.96); }
  50%      { opacity: 1;   transform: scale(1.04); }
}

.boot-text {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 3px;
  color: var(--accent);
  text-shadow: var(--glow-sm);
}
.boot-sub {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 1px;
  color: var(--text-muted);
  margin-top: -8px;
}
.ellipsis::after {
  content: '...';
  animation: dots 1.2s steps(4, end) infinite;
}
@keyframes dots {
  0%   { content: ''; }
  25%  { content: '.'; }
  50%  { content: '..'; }
  75%  { content: '...'; }
  100% { content: ''; }
}

.halt-icon { color: var(--amber); filter: drop-shadow(0 0 10px rgba(255, 176, 0, 0.5)); }
.overlay-state.halted .boot-text { color: var(--amber); }
.overlay-state.error { color: var(--red); }
.error-text { font-family: var(--font-mono); font-size: 13px; }
</style>
