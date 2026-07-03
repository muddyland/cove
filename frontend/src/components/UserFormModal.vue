<template>
  <BaseModal v-model="open" :title="editUser ? 'Edit User' : 'Create User'">
    <form @submit.prevent="handleSubmit" class="form">
      <div class="form-group">
        <label>Username</label>
        <input v-model="form.username" required />
      </div>
      <div class="form-group">
        <label>{{ editUser ? 'New Password (leave blank to keep)' : 'Password' }}</label>
        <input v-model="form.password" type="password" :required="!editUser" autocomplete="new-password" />
      </div>
      <div class="form-group checkbox">
        <label><input type="checkbox" v-model="form.is_admin" /> Admin</label>
      </div>
      <div v-if="error" class="form-error">{{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading">
          {{ editUser ? 'Save' : 'Create' }}
        </NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, reactive, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import type { User } from '@/types'

const open = defineModel<boolean>({ default: false })
const props = defineProps<{
  editUser?: User | null
  // Async so the modal can await the real API call: it stays open and shows the
  // inline error on failure (a taken username, weak password, …) instead of
  // vanishing and dropping the user's input behind an ephemeral toast.
  onSubmit: (payload: { username: string; password?: string; is_admin: boolean }) => Promise<void>
}>()

const loading = ref(false)
const error = ref('')
const form = reactive({ username: '', password: '', is_admin: false })

watch(() => props.editUser, (u) => {
  if (u) { form.username = u.username; form.is_admin = u.is_admin; form.password = '' }
  else { form.username = ''; form.password = ''; form.is_admin = false }
}, { immediate: true })

// Clear any stale error each time the modal is (re)opened.
watch(open, (o) => { if (o) error.value = '' })

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    const payload: { username: string; password?: string; is_admin: boolean } = {
      username: form.username,
      is_admin: form.is_admin,
    }
    if (form.password) payload.password = form.password
    await props.onSubmit(payload)
    open.value = false
  } catch (e: any) {
    error.value = e.message || 'Something went wrong'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.form { display: flex; flex-direction: column; gap: 16px; }
.form-actions { display: flex; gap: 8px; justify-content: flex-end; }
.checkbox label { display: flex; align-items: center; gap: 8px; color: var(--text); font-size: 14px; cursor: pointer; }
.checkbox input { width: auto; }
</style>
