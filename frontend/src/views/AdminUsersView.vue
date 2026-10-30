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
            <th>Zones</th>
            <th>Last Login</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td>{{ u.username }}</td>
            <td><span class="badge">{{ u.auth_provider }}</span></td>
            <td>
              {{ u.is_admin ? 'Admin' : 'User' }}<span v-if="u.docker_allowed && !u.is_admin" class="muted"> · docker</span>
              <span v-if="u.lan_only" class="muted" title="Local network only"> · LAN only</span>
              <span v-if="u.totp_enabled || u.passkey_count" class="muted" title="Two-factor sign-in set up"> · 2FA</span>
              <span v-else-if="u.require_2fa" class="warn" title="Sets up two-factor sign-in at the next sign-in"> · 2FA pending</span>
            </td>
            <td :class="{ muted: !u.zones_restricted || u.is_admin }">{{ zoneSummary(u) }}</td>
            <td>{{ u.last_login_at ? formatDate(u.last_login_at) : '—' }}</td>
            <td class="actions">
              <NeonButton variant="ghost" @click="openEdit(u)"><Pencil :size="13" /> Edit</NeonButton>
              <NeonButton
                v-if="u.id !== auth.user?.id"
                variant="ghost"
                title="Invalidate every login of this user (all devices)"
                @click="confirmRevoke(u)"
              ><LogOut :size="13" /> Sign out</NeonButton>
              <NeonButton
                v-if="u.totp_enabled || u.passkey_count"
                variant="ghost"
                title="Remove this user's authenticator app, passkeys and recovery codes (a lost phone or key)"
                @click="confirmReset(u)"
              ><ShieldOff :size="13" /> Reset 2FA</NeonButton>
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

    <UserFormModal v-model="showForm" :editUser="editTarget" :on-submit="handleSubmit" />
    <ConfirmModal
      v-model="showConfirm"
      title="Delete User"
      :message="`Delete '${deleteTarget?.username}'? Their workspaces are deleted too (running ones are stopped first). Home directories stay on disk.`"
      confirm-label="Delete"
      :loading="deleting"
      @confirm="handleDelete"
    />
    <ConfirmModal
      v-model="showReset"
      title="Reset two-factor sign-in"
      :message="`Remove ${resetTarget?.username}'s authenticator app, passkeys and recovery codes, and sign them out everywhere? Use this when they've lost their phone or key. They sign in with just their password next time${resetTarget?.require_2fa ? ', then set up a new second factor' : ''}.`"
      confirm-label="Reset 2FA"
      :loading="resetting"
      @confirm="handleReset"
    />
    <ConfirmModal
      v-model="showRevoke"
      title="Sign user out"
      :message="`Sign '${revokeTarget?.username}' out on every device? Their running workspaces keep running; they just have to log in again.`"
      confirm-label="Sign out"
      :loading="revoking"
      @confirm="handleRevoke"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { absoluteTime } from '@/utils/workspaceStatus'
import { ref, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import UserFormModal from '@/components/UserFormModal.vue'
import { zonesApi } from '@/api/zones'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { Plus, Pencil, Trash2, LogOut, ShieldOff } from 'lucide-vue-next'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'
import type { User, UserFormPayload } from '@/types'

const users = ref<User[]>([])
const ui = useUiStore()
const auth = useAuthStore()
const showForm = ref(false)
const showConfirm = ref(false)
const editTarget = ref<User | null>(null)
const deleteTarget = ref<User | null>(null)
const deleting = ref(false)

onMounted(async () => { users.value = await adminApi.users.list() })

// Zone names for the Zones column.
const zoneNames = ref<Record<number, string>>({})
onMounted(() => {
  zonesApi.list()
    .then(zs => { zoneNames.value = Object.fromEntries(zs.map(z => [z.id, z.id === 0 ? 'Local' : z.name])) })
    .catch(() => {})
})
function zoneSummary(u: User) {
  if (u.is_admin || !u.zones_restricted) return 'All'
  if (!u.allowed_zone_ids.length) return 'None'
  return u.allowed_zone_ids.map(id => zoneNames.value[id] ?? `#${id}`).join(', ')
}

// API timestamps are naive UTC; parse them as UTC, not local time.
const formatDate = absoluteTime
const showRevoke = ref(false)
const revokeTarget = ref<User | null>(null)
const revoking = ref(false)
function confirmRevoke(u: User) { revokeTarget.value = u; showRevoke.value = true }
async function handleRevoke() {
  if (!revokeTarget.value) return
  revoking.value = true
  try {
    await adminApi.users.revokeSessions(revokeTarget.value.id)
    showRevoke.value = false
    ui.toast(`${revokeTarget.value.username} was signed out everywhere`, 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { revoking.value = false }
}
const showReset = ref(false)
const resetTarget = ref<User | null>(null)
const resetting = ref(false)
function confirmReset(u: User) { resetTarget.value = u; showReset.value = true }
async function handleReset() {
  if (!resetTarget.value) return
  resetting.value = true
  try {
    const updated = await adminApi.users.reset2fa(resetTarget.value.id)
    const idx = users.value.findIndex(u => u.id === updated.id)
    if (idx !== -1) users.value[idx] = updated
    showReset.value = false
    ui.toast(`${updated.username}'s two-factor sign-in was reset`, 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { resetting.value = false }
}
function openCreate() { editTarget.value = null; showForm.value = true }
function openEdit(u: User) { editTarget.value = u; showForm.value = true }
function confirmDelete(u: User) { deleteTarget.value = u; showConfirm.value = true }

// Errors propagate to the modal (it stays open + shows them inline); we only
// toast + mutate the list on success.
async function handleSubmit(payload: UserFormPayload) {
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
.warn { color: var(--amber); }
@import '@/styles/tables.css';
.badge {
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 1px 6px; color: var(--text-muted);
}
.oidc-note {
  font-family: var(--font-mono); font-size: 11px; line-height: 1.5;
  color: var(--text-muted); margin: 0 0 16px;
}
</style>
