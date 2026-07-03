<template>
  <BaseModal :modelValue="modelValue" :title="title" @update:modelValue="$emit('update:modelValue', $event)" width="360px">
    <!-- A form so Enter confirms (the primary button is the submit + default
         focus target); Cancel is a plain button so it never submits. -->
    <form @submit.prevent="$emit('confirm')">
      <p class="message">{{ message }}</p>
      <slot />
      <div class="actions">
        <NeonButton type="button" variant="secondary" @click="$emit('update:modelValue', false)">Cancel</NeonButton>
        <NeonButton type="submit" variant="danger" :loading="loading" data-autofocus>{{ confirmLabel }}</NeonButton>
      </div>
    </form>
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
