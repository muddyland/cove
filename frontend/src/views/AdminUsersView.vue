<template>
  <AppShell>
    <div class="page-header">
      <h2>// USER REGISTRY</h2>
      <NeonButton v-if="!auth.oidcOnly" variant="primary" @click="openCreate"><Plus :size="14" /> Add User</NeonButton>
    </div>
    <p v-if="auth.oidcOnly" class="oidc-note">
      Local user creation is disabled — OIDC-only mode is active. Accounts are
      provisioned automatically on first SSO login.
    </p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Username</th>
            <th>Provider</th>
            <th>Role</th>
            <th>Last Login</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td>{{ u.username }}</td>
            <td><span class="badge">{{ u.auth_provider }}</span></td>
            <td>{{ u.is_admin ? 'Admin' : 'User' }}</td>
            <td>{{ u.last_login_at ? formatDate(u.last_login_at) : '—' }}</td>
            <td class="actions">
              <NeonButton variant="ghost" @click="openEdit(u)"><Pencil :size="13" /> Edit</NeonButton>
              <NeonButton
                v-if="u.id !== auth.user?.id"
                variant="danger"
                @click="confirmDelete(u)"
              ><Trash2 :size="13" /> Delete</NeonButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <UserFormModal v-model="showForm" :editUser="editTarget" @submit="handleSubmit" />
    <ConfirmModal
      v-model="showConfirm"
      title="Delete User"
      :message="`Delete '${deleteTarget?.username}'? All their workspaces will be stopped.`"
      confirm-label="Delete"
      :loading="deleting"
      @confirm="handleDelete"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import UserFormModal from '@/components/UserFormModal.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { Plus, Pencil, Trash2 } from 'lucide-vue-next'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'
import type { User } from '@/types'

const users = ref<User[]>([])
const ui = useUiStore()
const auth = useAuthStore()
const showForm = ref(false)
const showConfirm = ref(false)
const editTarget = ref<User | null>(null)
const deleteTarget = ref<User | null>(null)
const deleting = ref(false)

onMounted(async () => { users.value = await adminApi.users.list() })

function formatDate(d: string) { return new Date(d).toLocaleString() }
function openCreate() { editTarget.value = null; showForm.value = true }
function openEdit(u: User) { editTarget.value = u; showForm.value = true }
function confirmDelete(u: User) { deleteTarget.value = u; showConfirm.value = true }

async function handleSubmit(payload: { username: string; password?: string; is_admin: boolean }) {
  try {
    if (editTarget.value) {
      const updated = await adminApi.users.update(editTarget.value.id, payload)
      const idx = users.value.findIndex(u => u.id === editTarget.value!.id)
      if (idx !== -1) users.value[idx] = updated
      ui.toast('User updated', 'success')
    } else {
      const created = await adminApi.users.create({ ...payload, password: payload.password! })
      users.value.push(created)
      ui.toast('User created', 'success')
    }
  } catch (e: any) { ui.toast(e.message, 'error') }
}

async function handleDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await adminApi.users.remove(deleteTarget.value.id)
    users.value = users.value.filter(u => u.id !== deleteTarget.value!.id)
    showConfirm.value = false
    ui.toast('User deleted', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { deleting.value = false }
}
</script>

<style scoped>
@import '@/styles/tables.css';
.badge {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: 1px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 1px 6px; color: var(--text-muted);
}
.oidc-note {
  font-family: var(--font-mono); font-size: 11px; line-height: 1.5;
  color: var(--text-muted); margin: 0 0 16px;
}
</style>
