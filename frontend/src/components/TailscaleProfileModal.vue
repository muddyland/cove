<template>
  <BaseModal v-model="open" :title="profile ? 'Edit Tailscale profile' : 'Add Tailscale profile'">
    <form class="form" @submit.prevent="save">
      <div class="form-group">
        <label for="ts-name">Name</label>
        <input id="ts-name" v-model="f.name" required maxlength="64" autocomplete="off" placeholder="e.g. Home tailnet, Work (tagged)" />
      </div>
      <div class="form-group">
        <label for="ts-key">Auth key</label>
        <SecretInput
          id="ts-key" v-model="f.auth_key" :stored="hasKey" placeholder="tskey-auth-…"
          @clear="clearKey = true"
        />
        <p class="hint">
          A <strong>reusable</strong> key lets several workspaces join with it; a one-off key works
          once. Ephemeral keys remove the node from the tailnet when the workspace stops.
        </p>
      </div>
      <div class="form-group">
        <label for="ts-server">Control server <span class="opt">(optional)</span></label>
        <input id="ts-server" v-model="f.login_server" type="url" autocomplete="off" placeholder="https://headscale.example.com" />
        <p class="hint">Only for Headscale or another self-hosted control server. Leave empty for Tailscale.</p>
      </div>
      <p v-if="error" class="form-error" role="alert">⚠ {{ error }}</p>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" :disabled="saving" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="saving">{{ profile ? 'Save' : 'Add profile' }}</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import SecretInput from './SecretInput.vue'
import { usersApi, type TailscaleProfileInput } from '@/api/users'
import type { TailscaleProfile } from '@/types'

const props = defineProps<{ profile: TailscaleProfile | null }>()
const emit = defineEmits<{ saved: [TailscaleProfile] }>()
const open = defineModel<boolean>({ default: false })

const f = reactive({ name: '', auth_key: '', login_server: '' })
const clearKey = ref(false)
const hasKey = computed(() => !!props.profile?.has_auth_key && !clearKey.value)
const saving = ref(false)
const error = ref('')

watch(open, isOpen => {
  if (!isOpen) return
  f.name = props.profile?.name ?? ''
  f.auth_key = ''
  f.login_server = props.profile?.login_server ?? ''
  clearKey.value = false
  error.value = ''
}, { immediate: true })

async function save() {
  error.value = ''
  if (!props.profile && !f.auth_key.trim()) { error.value = 'Paste an auth key'; return }
  const payload: TailscaleProfileInput = { name: f.name.trim(), login_server: f.login_server.trim() }
  if (f.auth_key.trim()) payload.auth_key = f.auth_key.trim()
  else if (clearKey.value) payload.auth_key = ''
  saving.value = true
  try {
    const saved = props.profile
      ? await usersApi.updateTailscaleProfile(props.profile.id, payload)
      : await usersApi.createTailscaleProfile(payload)
    emit('saved', saved)
    open.value = false
  } catch (e: any) {
    error.value = e?.message || 'Saving failed'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.form { display: flex; flex-direction: column; gap: 14px; }
.form-actions { display: flex; justify-content: flex-end; gap: 10px; }
.hint { font-family: var(--font-mono); font-size: 12px; line-height: 1.5; color: var(--text-muted); margin: 6px 0 0; }
.opt { text-transform: none; letter-spacing: 0; }
.form-error { color: var(--red); font-size: 13px; margin: 0; }
</style>
