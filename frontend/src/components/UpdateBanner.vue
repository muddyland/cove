<template>
  <transition name="update-fade">
    <div v-if="needRefresh" class="update-bar" role="status">
      <span><Sparkles :size="14" /> New version available</span>
      <button type="button" @click="applyUpdate">Reload</button>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { Sparkles } from 'lucide-vue-next'
import { needRefresh, applyUpdate } from '@/pwa'
</script>

<style scoped>
.update-bar {
  position: fixed;
  left: 50%;
  bottom: calc(16px + env(safe-area-inset-bottom));
  transform: translateX(-50%);
  z-index: 1000;
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--surface-2);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  box-shadow: var(--glow-sm), var(--shadow);
  padding: 10px 12px 10px 14px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
  max-width: calc(100vw - 24px);
}
.update-bar > span { display: inline-flex; align-items: center; gap: 8px; color: var(--accent); }
.update-bar button {
  background: var(--accent);
  color: #00131a;
  border: none;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-weight: 700;
  letter-spacing: 1px;
  padding: 6px 12px;
  cursor: pointer;
  transition: box-shadow 0.15s;
}
.update-bar button:hover { box-shadow: var(--glow-sm); }

.update-fade-enter-active, .update-fade-leave-active { transition: opacity 0.2s, transform 0.2s; }
.update-fade-enter-from, .update-fade-leave-to { opacity: 0; transform: translate(-50%, 8px); }
</style>
