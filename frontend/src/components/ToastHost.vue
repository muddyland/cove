<template>
  <div class="toast-host" role="region" aria-label="Notifications" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="t in ui.toasts"
        :key="t.id"
        class="toast"
        :class="t.type"
        :role="t.type === 'error' ? 'alert' : 'status'"
        :title="'Dismiss'"
        @click="ui.dismiss(t.id)"
      >
        <component :is="iconFor(t.type)" class="toast-icon" :size="16" />
        <span>{{ t.message }}</span>
        <X v-if="t.type === 'error'" class="toast-close" :size="14" aria-hidden="true" />
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
import { CheckCircle2, XCircle, Info, X } from 'lucide-vue-next'
import { useUiStore } from '@/stores/ui'
import type { Toast } from '@/stores/ui'
const ui = useUiStore()

function iconFor(type: Toast['type']) {
  return { success: CheckCircle2, error: XCircle, info: Info }[type]
}
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
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: var(--radius);
  font-size: 13px;
  cursor: pointer;
  max-width: 320px;
  box-shadow: var(--shadow);
}
.toast-icon { flex-shrink: 0; }
/* Persistent error toasts get an explicit dismiss affordance. */
.toast-close { flex-shrink: 0; margin-left: 4px; opacity: 0.7; }
.toast.error:hover .toast-close { opacity: 1; }
.toast.success { background: var(--green); color: #0f1117; }
.toast.error { background: var(--red); color: #0f1117; }
.toast.info { background: var(--surface-2); border: 1px solid var(--border); color: var(--text); }

.toast-enter-active, .toast-leave-active { transition: all 0.25s; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(8px); }
</style>
