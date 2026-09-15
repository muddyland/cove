<template>
  <div class="loading-spinner" :class="{ block }" role="status" aria-live="polite">
    <span class="ring" :style="{ width: `${size}px`, height: `${size}px` }" />
    <span v-if="label" class="label">{{ label }}</span>
  </div>
</template>

<script setup lang="ts">
// A visible "still working" state: a spinning ring plus an optional label.
// `block` centers it with some room, for filling an otherwise empty panel.
withDefaults(defineProps<{ label?: string; size?: number; block?: boolean }>(), {
  label: 'Loading',
  size: 18,
  block: false,
})
</script>

<style scoped>
.loading-spinner {
  display: inline-flex; align-items: center; gap: 10px;
  color: var(--accent);
  font-family: var(--font-mono); font-size: 12px; letter-spacing: 1.5px; text-transform: uppercase;
}
.loading-spinner.block { display: flex; justify-content: center; padding: 28px 12px; width: 100%; box-sizing: border-box; }
.ring {
  flex-shrink: 0;
  border: 2px solid color-mix(in srgb, var(--accent) 22%, transparent);
  border-top-color: var(--accent);
  border-radius: 50%;
  box-shadow: var(--glow-sm);
  animation: spin 0.7s linear infinite;
}
.label { text-shadow: var(--glow-sm); }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .ring { animation-duration: 2s; }
}
</style>
