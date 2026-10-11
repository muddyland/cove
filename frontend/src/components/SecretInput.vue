<template>
  <!-- A secret field that never shows the stored value. When one is stored the
       placeholder says so (leave blank to keep) and a Clear link removes it. -->
  <div class="secret">
    <input
      :id="id"
      :value="modelValue"
      :type="plain ? 'text' : 'password'"
      :placeholder="stored ? 'stored ✓ — leave blank to keep' : placeholder"
      autocomplete="off"
      spellcheck="false"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    />
    <button v-if="stored && !modelValue" type="button" class="clear" @click="emit('clear')">Clear</button>
  </div>
</template>

<script setup lang="ts">
defineProps<{ id?: string; modelValue: string; stored?: boolean; placeholder?: string; plain?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [string]; clear: [] }>()
</script>

<style scoped>
.secret { position: relative; display: flex; align-items: center; }
.secret input { flex: 1; min-width: 0; padding-right: 58px; }
.clear {
  position: absolute; right: 6px;
  background: none; border: none; color: var(--text-muted); cursor: pointer;
  font-family: var(--font-mono); font-size: 11px; padding: 4px 6px; border-radius: var(--radius-sm);
}
.clear:hover { color: var(--red); }
</style>
