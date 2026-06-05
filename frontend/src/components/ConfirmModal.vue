<template>
  <BaseModal :modelValue="modelValue" :title="title" @update:modelValue="$emit('update:modelValue', $event)" width="360px">
    <p class="message">{{ message }}</p>
    <div class="actions">
      <NeonButton variant="secondary" @click="$emit('update:modelValue', false)">Cancel</NeonButton>
      <NeonButton variant="danger" :loading="loading" @click="$emit('confirm')">{{ confirmLabel }}</NeonButton>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'

defineProps<{
  modelValue: boolean
  title: string
  message: string
  confirmLabel?: string
  loading?: boolean
}>()
defineEmits(['update:modelValue', 'confirm'])
</script>

<style scoped>
.message { color: var(--text-muted); margin-bottom: 20px; line-height: 1.6; }
.actions { display: flex; gap: 8px; justify-content: flex-end; }
</style>
