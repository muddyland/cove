<template>
  <button class="btn" :class="[variant, { loading }]" :disabled="disabled || loading" v-bind="$attrs">
    <span v-if="loading" class="spinner" />
    <slot />
  </button>
</template>

<script setup lang="ts">
defineProps<{
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost' | 'success' | 'warn'
  loading?: boolean
  disabled?: boolean
}>()
</script>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1.2px;
  text-transform: uppercase;
  cursor: pointer;
  border: 1px solid transparent;
  font-family: var(--font-mono);
  transition: all 0.15s;
  position: relative;
}
.btn:disabled { opacity: 0.4; cursor: not-allowed; }

.primary {
  background: var(--accent-dim);
  color: var(--accent);
  border-color: var(--accent);
  box-shadow: inset 0 0 12px rgba(0, 245, 255, 0.05);
}
.primary:hover:not(:disabled) {
  background: rgba(0, 245, 255, 0.15);
  box-shadow: var(--glow-sm);
  color: #fff;
}

.secondary {
  background: transparent;
  color: var(--text-muted);
  border-color: var(--border);
}
.secondary:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
  box-shadow: var(--glow-sm);
}

.danger {
  background: transparent;
  color: var(--red);
  border-color: rgba(255, 32, 85, 0.4);
}
.danger:hover:not(:disabled) {
  background: rgba(255, 32, 85, 0.1);
  box-shadow: 0 0 8px var(--red), 0 0 20px rgba(255, 32, 85, 0.15);
}

.ghost {
  background: transparent;
  border-color: transparent;
  color: var(--text-muted);
}
.ghost:hover:not(:disabled) { color: var(--text); background: var(--surface-2); }

.success {
  background: transparent;
  color: var(--green);
  border-color: rgba(0, 255, 157, 0.4);
}
.success:hover:not(:disabled) {
  background: rgba(0, 255, 157, 0.1);
  box-shadow: 0 0 8px var(--green), 0 0 20px rgba(0, 255, 157, 0.15);
  color: #fff;
}

.warn {
  background: transparent;
  color: var(--amber);
  border-color: rgba(255, 170, 0, 0.4);
}
.warn:hover:not(:disabled) {
  background: rgba(255, 170, 0, 0.1);
  box-shadow: 0 0 8px var(--amber), 0 0 20px rgba(255, 170, 0, 0.15);
  color: #fff;
}

.spinner {
  width: 10px;
  height: 10px;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
