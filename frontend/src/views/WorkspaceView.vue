<template>
  <div class="workspace-page">
    <div class="top-bar">
      <RouterLink to="/" class="back-link">← GRID</RouterLink>
      <div class="ws-info">
        <div class="ws-switcher">
          <button class="ws-switch-btn" :class="{ open: menuOpen }" @click.stop="menuOpen = !menuOpen">
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
        <StatusBadge v-if="ws" :status="ws.status" />
      </div>
      <div class="top-actions">
        <button
          v-if="ws?.status === 'running'"
          class="bar-btn crt-btn"
          :class="{ active: ui.crt }"
          :title="ui.crt ? 'CRT effect on' : 'CRT effect off'"
          @click="ui.toggleCrt()"
        ><ScanLine :size="14" /> CRT</button>
        <button
          v-if="ws?.status === 'running'"
          class="bar-btn fs-btn"
          :title="isFullscreen ? 'Exit fullscreen' : 'Fullscreen'"
          @click="toggleFullscreen"
        ><component :is="isFullscreen ? Minimize : Maximize" :size="14" /> {{ isFullscreen ? 'WINDOW' : 'FULL' }}</button>
        <NeonButton v-if="ws?.status === 'running'" variant="danger" :loading="stopping" @click="handleStop">HALT</NeonButton>
      </div>
    </div>

    <div v-if="!ws || ws.status === 'creating'" class="overlay-state">
      <img class="boot-icon" src="/favicon.svg" alt="" />
      <p class="boot-text">{{ installing ? 'PROVISIONING NODE' : 'BOOTING NODE' }}<span class="ellipsis" /></p>
      <p v-if="installing" class="boot-sub">Installing packages &amp; proot-apps — this can take a few minutes.</p>
    </div>

    <div class="frame-wrap" v-else-if="ws.status === 'running' && streamUrl" ref="frameWrap">
      <iframe
        :src="streamUrl"
        class="workspace-frame"
        allow="autoplay; clipboard-read; clipboard-write; fullscreen; camera; microphone"
        allowfullscreen
      />
      <!-- CRT scanline / flicker overlay (pointer-events:none so the stream stays
           interactive). Toggled per-user from the top bar. -->
      <div v-if="ui.crt" class="crt-overlay" aria-hidden="true" />
      <!-- Branding overlay -->
      <div class="branding">
        <img src="/favicon.svg" alt="" />
        <span>COVE</span>
      </div>
    </div>

    <div v-else-if="ws?.status === 'error'" class="overlay-state error">
      <p class="error-text">⚠ {{ ws.error_message || 'Node failed to boot' }}</p>
      <NeonButton variant="secondary" @click="$router.push('/')">← RETURN TO GRID</NeonButton>
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
import { ScanLine, Maximize, Minimize, ChevronDown, Power } from 'lucide-vue-next'

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
  router.push(`/workspace/${w.id}`)
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

async function handleStop() {
  stopping.value = true
  try {
    await store.stop(wsId.value)
    router.push('/')
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    stopping.value = false
  }
}
</script>

<style scoped>
.workspace-page { display: flex; flex-direction: column; height: 100vh; background: #000; overflow: hidden; }

.top-bar {
  display: flex; align-items: center; gap: 16px;
  padding: 0 16px;
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
.top-actions { margin-left: auto; display: flex; align-items: center; gap: 10px; }

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

/* CRT — amber/retro. Outlined when off, filled when on. */
.crt-btn {
  color: var(--amber);
  border-color: rgba(255, 170, 0, 0.45);
}
.crt-btn:hover {
  color: #fff;
  border-color: var(--amber);
  box-shadow: 0 0 8px rgba(255, 170, 0, 0.5);
}
.crt-btn.active {
  color: #1a1206;
  background: var(--amber);
  border-color: var(--amber);
  box-shadow: 0 0 10px rgba(255, 170, 0, 0.6);
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

.branding {
  position: absolute;
  bottom: 12px;
  right: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--accent);
  opacity: 0.35;
  pointer-events: none;
  transition: opacity 0.3s;
}
.branding:hover { opacity: 0.7; }
.branding img { width: 20px; height: 20px; }
.branding span {
  font-family: var(--font-display);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 3px;
  text-shadow: var(--glow-sm);
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

.overlay-state.error { color: var(--red); }
.error-text { font-family: var(--font-mono); font-size: 13px; }
</style>
