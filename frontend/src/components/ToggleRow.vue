<template>
  <div class="toggle-wrap">
    <button
      type="button"
      role="switch"
      class="toggle-row"
      :class="{ on: modelValue }"
      :aria-checked="modelValue"
      :aria-describedby="$slots.hint ? hintId : undefined"
      :disabled="disabled"
      @click="toggle"
    >
      <span class="toggle-main">
        <span v-if="$slots.icon" class="toggle-ico" aria-hidden="true"><slot name="icon" /></span>
        <span class="toggle-label"><slot /></span>
      </span>
      <span class="switch" aria-hidden="true"><span class="knob" /></span>
    </button>
    <!-- Kept outside the button so the switch's accessible name stays the label
         alone; screen readers still reach it through aria-describedby. -->
    <p v-if="$slots.hint" :id="hintId" class="toggle-hint"><slot name="hint" /></p>
  </div>
</template>

<script setup lang="ts">
import { useId } from 'vue'

const modelValue = defineModel<boolean>({ required: true })
const props = defineProps<{ disabled?: boolean }>()
const hintId = useId()

function toggle() {
  if (!props.disabled) modelValue.value = !modelValue.value
}
</script>

<style scoped>
.toggle-wrap { display: flex; flex-direction: column; gap: 6px; }

.toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  width: 100%;
  padding: 9px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-family: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.toggle-row:hover:not(:disabled) { border-color: var(--accent); }
.toggle-row.on { border-color: var(--accent); background: var(--accent-dim); }
.toggle-row:disabled { opacity: 0.45; cursor: not-allowed; }

.toggle-main { display: flex; align-items: center; gap: 9px; min-width: 0; }
.toggle-ico { display: inline-flex; flex: none; color: var(--text-muted); }
.toggle-row.on .toggle-ico { color: var(--accent); }
.toggle-label { line-height: 1.35; }

.toggle-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-muted);
}
.toggle-hint :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.9em;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0 4px;
}

.switch {
  flex: none;
  position: relative;
  width: 34px;
  height: 18px;
  border-radius: 999px;
  background: var(--bg);
  border: 1px solid var(--border);
  transition: background 0.15s, border-color 0.15s;
}
.toggle-row.on .switch { background: var(--accent); border-color: var(--accent); }
.knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--text-muted);
  transition: transform 0.15s, background 0.15s;
}
.toggle-row.on .knob { transform: translateX(16px); background: #06060f; }

@media (prefers-reduced-motion: reduce) {
  .toggle-row, .switch, .knob { transition: none; }
}
</style>
