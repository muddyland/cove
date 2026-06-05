<template>
  <div class="toast-host">
    <TransitionGroup name="toast">
      <div
        v-for="t in ui.toasts"
        :key="t.id"
        class="toast"
        :class="t.type"
        @click="ui.dismiss(t.id)"
      >
        {{ t.message }}
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
const ui = useUiStore()
</script>

<style scoped>
.toast-host {
  position: fixed;
  bottom: 24px;
  right: 24px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 9999;
}
.toast {
  padding: 10px 16px;
  border-radius: var(--radius);
  font-size: 13px;
  cursor: pointer;
  max-width: 320px;
  box-shadow: var(--shadow);
}
.toast.success { background: var(--green); color: #0f1117; }
.toast.error { background: var(--red); color: #0f1117; }
.toast.info { background: var(--surface-2); border: 1px solid var(--border); color: var(--text); }

.toast-enter-active, .toast-leave-active { transition: all 0.25s; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(8px); }
</style>
