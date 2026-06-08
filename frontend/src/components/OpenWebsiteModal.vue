<template>
  <BaseModal v-model="open" title="Open Website">
    <form @submit.prevent="handleSubmit" class="form">
      <div class="form-group">
        <label>// website url(s)</label>
        <textarea
          v-model="url"
          rows="3"
          placeholder="https://app.example.com&#10;https://another.example.com"
          required
          autofocus
        />
        <p class="hint">One URL per line — each opens in its own tab (up to 6). Multiple tabs open full-screen with a tab bar.</p>
      </div>
      <div class="form-group">
        <label>// browser</label>
        <select v-model="browserId" required>
          <option v-if="!browsers.length" value="" disabled>No browser images — sync the catalog first</option>
          <option v-for="b in browsers" :key="b.id" :value="b.id">{{ b.name }}</option>
        </select>
      </div>
      <label class="checkbox-row">
        <input type="checkbox" :checked="useTailscale" @change="pickTailscale($event)" />
        <span>Route through Tailscale</span>
      </label>
      <template v-if="useTailscale">
        <div class="form-group ts-field">
          <label>// exit node (optional)</label>
          <input v-model="tsExitNode" type="text" placeholder="us-nyc-1 or 100.x.y.z" />
        </div>
        <label class="checkbox-row ts-field">
          <input type="checkbox" v-model="tsAcceptRoutes" />
          <span>Accept routes</span>
        </label>
        <label class="checkbox-row ts-field">
          <input type="checkbox" v-model="tsAcceptDns" />
          <span>Accept DNS</span>
        </label>
      </template>
      <template v-if="gluetunReady">
        <label class="checkbox-row">
          <input type="checkbox" :checked="useGluetun" @change="pickGluetun($event)" />
          <span>Route through Gluetun (VPN)</span>
        </label>
        <p v-if="useGluetun" class="hint ts-field">
          Uses your Gluetun VPN config (Preferences → Gluetun). All egress goes
          through the VPN tunnel.
        </p>
      </template>
      <label class="checkbox-row">
        <input type="checkbox" v-model="ephemeral" />
        <span>Ephemeral (no saved data — wiped when halted)</span>
      </label>
      <p v-if="ephemeral" class="hint ts-field">
        Cookies, history, and downloads live only in the container and are
        discarded on halt. Nothing is written to persistent storage.
      </p>
      <div v-if="error" class="form-error">⚠ {{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading" :disabled="!browsers.length">Launch</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import { imagesApi } from '@/api/images'
import { usersApi } from '@/api/users'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type { WorkspaceImage } from '@/types'

const open = defineModel<boolean>({ default: false })

const images = ref<WorkspaceImage[]>([])
const url = ref('')
const browserId = ref<number | ''>('')
const useTailscale = ref(false)
const tsExitNode = ref('')
const tsAcceptRoutes = ref(true)
const tsAcceptDns = ref(true)
const ephemeral = ref(false)
const useGluetun = ref(false)
const gluetunReady = ref(false)

// Tailscale and Gluetun are mutually exclusive routing modes.
function pickTailscale(e: Event) {
  useTailscale.value = (e.target as HTMLInputElement).checked
  if (useTailscale.value) useGluetun.value = false
}
function pickGluetun(e: Event) {
  useGluetun.value = (e.target as HTMLInputElement).checked
  if (useGluetun.value) useTailscale.value = false
}
const loading = ref(false)
const error = ref('')
const store = useWorkspacesStore()
const ui = useUiStore()
const router = useRouter()

const browsers = computed(() => images.value.filter(i => i.image_type === 'browser' || i.url_env))

onMounted(async () => {
  images.value = await imagesApi.list()
  if (browsers.value.length) browserId.value = browsers.value[0].id
  try {
    const g = await usersApi.getGluetun()
    gluetunReady.value = g.enabled && g.has_config
  } catch {
    // Non-fatal: Gluetun toggle just stays hidden.
  }
})

function deriveName(u: string): string {
  const first = u.trim().split(/\s+/)[0] || ''
  try {
    return new URL(first).hostname.replace(/^www\./, '')
  } catch {
    return 'website'
  }
}

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    const ws = await store.launch({
      name: deriveName(url.value),
      image_id: browserId.value as number,
      workspace_type: 'browser',
      target_url: url.value,
      ephemeral: ephemeral.value,
      use_gluetun: useGluetun.value,
      use_tailscale: useTailscale.value,
      ...(useTailscale.value
        ? {
            ts_exit_node: tsExitNode.value || undefined,
            ts_accept_routes: tsAcceptRoutes.value,
            ts_accept_dns: tsAcceptDns.value,
          }
        : {}),
    })
    open.value = false
    ui.toast(`Opening ${deriveName(url.value)}…`, 'info')
    url.value = ''
    useTailscale.value = false
    tsExitNode.value = ''
    tsAcceptRoutes.value = true
    tsAcceptDns.value = true
    ephemeral.value = false
    useGluetun.value = false
    router.push(`/app/workspace/${ws.id}`)
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.form { display: flex; flex-direction: column; gap: 16px; }
.form-actions { display: flex; gap: 8px; justify-content: flex-end; }
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
}
.checkbox-row input { width: auto; margin: 0; }
.ts-field { padding-left: 24px; border-left: 1px solid var(--border); }
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 0; }
</style>
