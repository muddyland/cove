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
      <div class="form-group checkbox">
        <label><input type="checkbox" v-model="form.docker_allowed" /> Allow Docker-in-Docker</label>
        <p class="hint">
          Lets this account enable Docker on its workspaces (when the deployment-wide
          toggle is on). The nested daemon is <strong>privileged</strong>: whoever holds it
          is effectively root on the host. Admins always have it.
        </p>
      </div>
      <div class="form-group checkbox">
        <label>
          <input type="checkbox" v-model="form.zones_restricted" :disabled="form.is_admin" />
          Limit to specific zones
        </label>
        <p class="hint">
          <template v-if="form.is_admin">Admins can always use every zone.</template>
          <template v-else-if="!form.zones_restricted">This account can run workspaces on every zone.</template>
          <template v-else>
            Workspaces can only be launched, started, cloned, migrated or given new files on the zones
            ticked below. Existing workspaces elsewhere stay, but can't start until migrated.
          </template>
        </p>
        <div v-if="form.zones_restricted && !form.is_admin" class="zone-list">
          <label v-for="z in zones" :key="z.id">
            <input type="checkbox" :value="z.id" v-model="form.allowed_zone_ids" />
            {{ z.id === 0 ? 'Local (this Cove host)' : z.name }}
            <span v-if="z.status !== 'enrolled' && z.id !== 0" class="zone-status">{{ z.status }}</span>
          </label>
          <p v-if="!form.allowed_zone_ids.length" class="hint warn">No zones ticked: this account can't run any workspace.</p>
        </div>
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
import { zonesApi } from '@/api/zones'
import type { User, UserFormPayload, Zone } from '@/types'

const open = defineModel<boolean>({ default: false })
const props = defineProps<{
  editUser?: User | null
  // Async so the modal can await the real API call: it stays open and shows the
  // inline error on failure (a taken username, weak password, …) instead of
  // vanishing and dropping the user's input behind an ephemeral toast.
  onSubmit: (payload: UserFormPayload) => Promise<void>
}>()

const loading = ref(false)
const error = ref('')
const form = reactive({
  username: '', password: '', is_admin: false, docker_allowed: false,
  zones_restricted: false, allowed_zone_ids: [] as number[],
})

watch(() => props.editUser, (u) => {
  form.username = u?.username ?? ''
  form.password = ''
  form.is_admin = !!u?.is_admin
  form.docker_allowed = !!u?.docker_allowed
  form.zones_restricted = !!u?.zones_restricted
  form.allowed_zone_ids = [...(u?.allowed_zone_ids ?? [])]
}, { immediate: true })

// Zones to choose from (Local first). Loaded when the modal opens.
const zones = ref<Zone[]>([])

// Clear any stale error each time the modal is (re)opened.
watch(open, (o) => {
  if (!o) return
  error.value = ''
  zonesApi.list().then(z => { zones.value = z }).catch(() => { zones.value = [] })
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    const payload: UserFormPayload = {
      username: form.username,
      is_admin: form.is_admin,
      docker_allowed: form.docker_allowed,
      zones_restricted: form.zones_restricted,
      allowed_zone_ids: form.zones_restricted ? form.allowed_zone_ids : [],
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
.zone-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.zone-list label { font-size: 13px; }
.zone-status { font-family: var(--font-mono); font-size: 10px; text-transform: uppercase; color: var(--amber); margin-left: 4px; }
.hint.warn { color: var(--amber); }
</style>
