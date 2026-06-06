<template>
  <div class="workspace-page">
    <div class="top-bar">
      <RouterLink to="/" class="back-link">← GRID</RouterLink>
      <div class="ws-info">
        <span class="ws-name">{{ ws?.name }}</span>
        <StatusBadge v-if="ws" :status="ws.status" />
      </div>
      <div class="top-actions">
        <button
          v-if="ws?.status === 'running'"
          class="crt-btn"
          :class="{ active: ui.crt }"
          :title="ui.crt ? 'CRT effect on' : 'CRT effect off'"
          @click="ui.toggleCrt()"
        ><ScanLine :size="14" /> CRT</button>
        <NeonButton v-if="ws?.status === 'running'" variant="secondary" :loading="stopping" @click="handleStop">HALT</NeonButton>
      </div>
    </div>

    <div v-if="!ws || ws.status === 'creating'" class="overlay-state">
      <img class="boot-icon" src="/favicon.svg" alt="" />
      <p class="boot-text">{{ installing ? 'PROVISIONING NODE' : 'BOOTING NODE' }}<span class="ellipsis" /></p>
      <p v-if="installing" class="boot-sub">Installing packages &amp; proot-apps — this can take a few minutes.</p>
    </div>

    <div class="frame-wrap" v-else-if="ws.status === 'running' && streamUrl">
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
import { ScanLine } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const store = useWorkspacesStore()
const ui = useUiStore()

const wsId = computed(() => Number(route.params.id))
const ws = computed(() => store.items.find(w => w.id === wsId.value))
const installing = computed(() => !!(ws.value?.install_packages || ws.value?.proot_apps))
const stopping = ref(false)
const streamUrl = ref<string | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

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
  if (!ws.value) await store.fetch()
  startPollIfNeeded()
  await loadStreamUrl()
})
onUnmounted(() => {
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
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px;
  color: var(--text-muted); text-decoration: none; flex-shrink: 0;
}
.back-link:hover { color: var(--accent); }

.ws-info { display: flex; align-items: center; gap: 10px; flex: 1; }
.ws-name { font-family: var(--font-mono); font-size: 12px; letter-spacing: 1px; }
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

.crt-btn {
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
.crt-btn:hover { color: var(--text); border-color: var(--text-muted); }
.crt-btn.active {
  color: var(--accent);
  border-color: var(--accent);
  text-shadow: var(--glow-sm);
}
.crt-btn.active svg { filter: drop-shadow(var(--glow-sm)); }

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
