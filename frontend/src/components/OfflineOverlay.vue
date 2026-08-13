<template>
  <Teleport to="body">
    <Transition name="offline-fade">
      <div v-if="conn.offline" class="offline-overlay" role="alertdialog" aria-live="assertive" aria-label="Server unreachable">
        <div class="offline-grid" aria-hidden="true" />
        <div class="offline-box">
          <img class="boot-icon" src="/favicon.svg" alt="" />
          <p class="boot-text">SERVER OFFLINE<span class="ellipsis" /></p>
          <p class="boot-sub">{{ subline }}</p>
          <div class="offline-actions">
            <NeonButton variant="secondary" :loading="conn.checking" @click="conn.retryNow()">
              <RefreshCw :size="14" /> RETRY
            </NeonButton>
            <span class="retry-hint">{{ retryHint }}</span>
          </div>
        </div>
        <div class="offline-scan" aria-hidden="true" />
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * Full-screen takeover for "the backend isn't answering".
 *
 * Deliberately the same shape as the BOOTING / PROVISIONING NODE states in the
 * workspace view — pulsing mark, mono caption, one line of plain explanation —
 * because to the person looking at it this is the same kind of moment: something
 * is coming up and there's nothing to do but wait. The difference is the colour
 * (amber, not accent) and the RETRY button, since unlike a booting node this one
 * might need a nudge.
 *
 * It covers everything rather than sitting in a corner: with the server gone, no
 * control on the page underneath does anything, and a screen of dead buttons
 * reads as a broken app rather than an absent server. Retries are automatic —
 * the button and countdown just make that visible.
 */
import { computed } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import NeonButton from './NeonButton.vue'
import { useConnectionStore } from '@/stores/connection'

const conn = useConnectionStore()

// The browser knowing it has no network at all is a different problem with a
// different fix, so say so rather than pointing the finger at the server.
const subline = computed(() =>
  navigator.onLine
    ? "Cove's backend isn't responding. It may be restarting, or the connection to it dropped."
    : 'This device is offline. Cove will reconnect on its own once the network is back.',
)

const retryHint = computed(() => {
  if (conn.checking) return 'Reconnecting…'
  return conn.retryIn > 0 ? `Retrying in ${conn.retryIn}s` : 'Retrying…'
})
</script>

<style scoped>
.offline-overlay {
  position: fixed;
  inset: 0;
  /* Under the toast host (9999) so a queued error is still readable, over
     everything else including modals. */
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(6, 6, 15, 0.94);
  backdrop-filter: blur(3px);
  overflow: hidden;
}

/* Faint grid + scanline: the same backdrop the rest of the app uses, so an
   outage still looks like Cove rather than a browser error page. */
.offline-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(var(--border) 1px, transparent 1px),
    linear-gradient(90deg, var(--border) 1px, transparent 1px);
  background-size: 44px 44px;
  opacity: 0.25;
  mask-image: radial-gradient(circle at 50% 45%, #000 0%, transparent 72%);
}
.offline-scan {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: repeating-linear-gradient(
    to bottom,
    rgba(0, 0, 0, 0) 0px,
    rgba(0, 0, 0, 0) 2px,
    rgba(0, 0, 0, 0.22) 3px
  );
  opacity: 0.5;
}

.offline-box {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
  text-align: center;
  max-width: 420px;
}

/* Matches the boot overlay's pulsing mark, tinted amber for a fault state. */
.boot-icon {
  width: 64px;
  height: 64px;
  animation: bootpulse 1.6s ease-in-out infinite;
  filter: drop-shadow(0 0 12px rgba(255, 170, 0, 0.45));
}
@keyframes bootpulse {
  0%, 100% { opacity: 0.5; transform: scale(0.96); }
  50%      { opacity: 1;   transform: scale(1.04); }
}

.boot-text {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 3px;
  color: var(--amber);
  text-shadow: 0 0 4px rgba(255, 170, 0, 0.6), 0 0 12px rgba(255, 170, 0, 0.2);
}
.boot-sub {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 1px;
  line-height: 1.7;
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

.offline-actions {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.retry-hint {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 2px;
  color: var(--text-muted);
}

.offline-fade-enter-active,
.offline-fade-leave-active {
  transition: opacity 0.2s ease;
}
.offline-fade-enter-from,
.offline-fade-leave-to {
  opacity: 0;
}

/* Respect a reduced-motion preference: the pulse and dots are decoration, and
   this overlay can be on screen for a long outage. */
@media (prefers-reduced-motion: reduce) {
  .boot-icon { animation: none; }
  .ellipsis::after { animation: none; content: '...'; }
}
</style>
