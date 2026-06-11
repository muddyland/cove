<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="modelValue" class="overlay" @mousedown.self="$emit('update:modelValue', false)">
        <div class="modal" :style="{ width: width || '480px' }">
          <div class="modal-header">
            <span class="modal-title">{{ title }}</span>
            <button class="close-btn" @click="$emit('update:modelValue', false)" aria-label="Close"><X :size="16" /></button>
          </div>
          <div class="modal-body">
            <slot />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { X } from 'lucide-vue-next'
defineProps<{ modelValue: boolean; title: string; width?: string }>()
defineEmits(['update:modelValue'])
</script>

<style scoped>
.overlay {
  position: fixed; inset: 0;
  /* Solid scrim only — no backdrop-filter. A full-viewport backdrop blur keeps a
     GPU compositing layer alive for as long as any modal is mounted, which can
     drop the browser's hardware video-overlay path and make video stutter in
     other windows/screens. The 0.8 black already separates the modal cleanly. */
  background: rgba(0, 0, 0, 0.8);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal {
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  box-shadow: var(--glow), var(--shadow);
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 64px);
  display: flex; flex-direction: column;
}
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  position: relative;
}
.modal-header::after {
  content: '';
  position: absolute;
  bottom: -1px; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0.5;
}
.modal-title {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--accent);
  text-shadow: var(--glow-sm);
}
.close-btn {
  background: none; border: none; color: var(--text-muted);
  cursor: pointer; font-size: 12px; font-family: var(--font-mono);
  letter-spacing: 1px; padding: 2px 6px; transition: color 0.15s;
  display: inline-flex; align-items: center;
}
.close-btn:hover { color: var(--red); text-shadow: 0 0 8px var(--red); }
.modal-body { padding: 20px; overflow-y: auto; }

.modal-enter-active, .modal-leave-active { transition: all 0.2s; }
.modal-enter-from, .modal-leave-to { opacity: 0; transform: scale(0.95) translateY(-12px); }
</style>
