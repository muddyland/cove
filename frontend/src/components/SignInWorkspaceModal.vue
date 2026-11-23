<template>
  <BaseModal v-model="open" :title="`Sign-in workspace · ${user?.username ?? ''}`" width="520px">
    <form class="form" @submit.prevent="save">
      <LoadingSpinner v-if="loading" block />
      <template v-else>
        <div class="form-group">
          <label for="sw-choice">After {{ user?.username }} signs in, open</label>
          <select id="sw-choice" v-model="choice">
            <option value="">The dashboard</option>
            <option v-for="w in choices" :key="w.public_id" :value="w.public_id">
              {{ w.name }} ({{ w.image_name }}{{ w.status === 'stopped' ? '' : ` · ${w.status}` }})
            </option>
            <option :value="NEW">New workspace…</option>
          </select>
          <p v-if="!workspaces.length && choice !== NEW" class="hint">
            {{ user?.username }} has no workspaces yet. Pick <em>New workspace…</em> to create one, so it's
            waiting for them at their first sign-in.
          </p>
        </div>

        <fieldset v-if="choice === NEW" class="new">
          <legend>New workspace for {{ user?.username }}</legend>
          <div class="form-group">
            <label for="sw-name">Name</label>
            <input id="sw-name" v-model="draft.name" required maxlength="128" placeholder="Desktop" />
          </div>
          <div class="form-group">
            <label for="sw-image">Image</label>
            <select id="sw-image" v-model.number="draft.image_id" required>
              <option :value="0" disabled>— choose —</option>
              <option v-for="i in images" :key="i.id" :value="i.id">{{ i.name }}</option>
            </select>
          </div>
          <div v-if="urlCapable" class="form-group">
            <label for="sw-url">Start-up URL{{ isLink ? '' : ' (optional)' }}</label>
            <input id="sw-url" v-model="draft.target_url" :required="isLink" placeholder="https://" />
          </div>
          <div v-if="zoneChoices.length > 1" class="form-group">
            <label for="sw-zone">Zone</label>
            <select id="sw-zone" v-model.number="draft.zone_id">
              <option v-for="z in zoneChoices" :key="z.id" :value="z.id">{{ z.id === 0 ? 'Local (this Cove host)' : z.name }}</option>
            </select>
          </div>
          <p v-else-if="!zoneChoices.length" class="hint warn">
            {{ user?.username }} can't use any zone (Edit → Limit to specific zones), so no workspace can run for them.
          </p>
          <p class="hint">
            It's created and started now, under {{ user?.username }}'s account, with the default launch
            options; they can change those like on any of their workspaces.
          </p>
        </fieldset>

        <div class="form-group checkbox">
          <label><input v-model="locked" type="checkbox" :disabled="!choice" /> Lock it</label>
          <p class="hint">
            <template v-if="!choice">Pick a workspace to lock {{ user?.username }} to.</template>
            <template v-else>
              {{ user?.username }} can't change this in Preferences or delete the workspace. Without the lock
              it's only a starting point they can change.
            </template>
          </p>
        </div>

        <div v-if="error" class="form-error">{{ error }}</div>
        <div class="form-actions">
          <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
          <NeonButton type="submit" variant="primary" :loading="saving" :disabled="choice === NEW && !canCreate">
            {{ choice === NEW ? 'Create & save' : 'Save' }}
          </NeonButton>
        </div>
      </template>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import LoadingSpinner from './LoadingSpinner.vue'
import { adminApi } from '@/api/admin'
import { imagesApi } from '@/api/images'
import { zonesApi } from '@/api/zones'
import type { User, Workspace, WorkspaceImage, Zone } from '@/types'

const NEW = '__new__'

const open = defineModel<boolean>({ default: false })
const props = defineProps<{ user: User | null }>()
const emit = defineEmits<{ saved: [user: User] }>()

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const workspaces = ref<Workspace[]>([])
const images = ref<WorkspaceImage[]>([])
const zones = ref<Zone[]>([])
const choice = ref('')
const locked = ref(false)
const draft = reactive({ name: '', image_id: 0, zone_id: 0, target_url: '' })

// A workspace that deletes itself when it stops can't be the sign-in one.
const choices = computed(() => workspaces.value.filter(w => !w.auto_remove))
const image = computed(() => images.value.find(i => i.id === draft.image_id))
const isLink = computed(() => image.value?.image_type === 'link')
const urlCapable = computed(() => isLink.value || !!image.value?.url_env)
// Zones this account may run workspaces on (admins: all of them).
const zoneChoices = computed(() => {
  const u = props.user
  const usable = zones.value.filter(z => z.id === 0 || z.status === 'enrolled')
  if (!u || u.is_admin || !u.zones_restricted) return usable
  return usable.filter(z => u.allowed_zone_ids.includes(z.id))
})
const canCreate = computed(() =>
  !!draft.name.trim() && !!draft.image_id && zoneChoices.value.length > 0 && (!isLink.value || !!draft.target_url.trim()),
)

watch(choice, v => { if (!v) locked.value = false })

watch(open, async o => {
  if (!o || !props.user) return
  const u = props.user
  error.value = ''
  choice.value = u.auto_launch_workspace_id ?? ''
  locked.value = u.auto_launch_locked
  Object.assign(draft, { name: '', image_id: 0, zone_id: 0, target_url: '' })
  loading.value = true
  try {
    const [ws, imgs, zs] = await Promise.all([
      adminApi.users.workspaces(u.id),
      imagesApi.list(),
      zonesApi.list().catch(() => [] as Zone[]),
    ])
    workspaces.value = ws
    images.value = imgs.filter(i => i.enabled)
    zones.value = zs
    if (!zoneChoices.value.some(z => z.id === draft.zone_id) && zoneChoices.value.length) {
      draft.zone_id = zoneChoices.value[0].id
    }
    // Nothing to pick yet: start on the create form.
    if (!ws.length) choice.value = NEW
  } catch (e: any) {
    error.value = e?.message || 'Could not load the account’s workspaces'
  } finally {
    loading.value = false
  }
}, { immediate: true })

async function save() {
  const u = props.user
  if (!u) return
  error.value = ''
  saving.value = true
  try {
    if (choice.value === NEW) {
      const ws = await adminApi.users.createWorkspace(u.id, {
        name: draft.name.trim(),
        image_id: draft.image_id,
        zone_id: draft.zone_id,
        target_url: urlCapable.value ? draft.target_url.trim() || null : null,
        auto_launch: true,
        locked: locked.value,
      })
      emit('saved', { ...u, auto_launch_workspace_id: ws.public_id, auto_launch_locked: locked.value })
    } else {
      emit('saved', await adminApi.users.setAutoLaunch(u.id, choice.value || null, locked.value))
    }
    open.value = false
  } catch (e: any) {
    error.value = e?.message || 'Something went wrong'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.form { display: flex; flex-direction: column; gap: 16px; }
.form-actions { display: flex; gap: 8px; justify-content: flex-end; }
.checkbox label { display: flex; align-items: center; gap: 8px; color: var(--text); font-size: 14px; cursor: pointer; }
.checkbox input { width: auto; }
.new { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 12px 10px; margin: 0; display: flex; flex-direction: column; gap: 12px; }
.new legend { padding: 0 6px; font-family: var(--font-mono); font-size: 11px; letter-spacing: 1.5px; text-transform: uppercase; color: var(--text-muted); }
.hint.warn { color: var(--amber); }
</style>
