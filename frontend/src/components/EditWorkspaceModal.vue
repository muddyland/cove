<template>
  <BaseModal v-model="open" title="Edit Workspace">
    <form @submit.prevent="handleSubmit" class="form">
      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="My Desktop" required />
      </div>

      <div v-if="urlCapable" class="form-group">
        <label>Target URL(s)</label>
        <textarea v-model="form.target_url" rows="2" placeholder="https://example.com" />
        <p class="hint">One URL per line — each opens in its own tab (up to 6).</p>
      </div>
      <label v-if="urlCapable && urlCount <= 1" class="checkbox-row">
        <input type="checkbox" v-model="form.kiosk" />
        <span>Kiosk mode (full-screen, no browser chrome)</span>
      </label>
      <p v-if="urlCapable && urlCount > 1" class="hint">
        Multiple tabs open full-screen with a tab bar — kiosk lock is unavailable.
      </p>
      <template v-if="urlCapable && urlCount <= 1 && form.kiosk">
        <label class="checkbox-row ts-field">
          <input type="checkbox" v-model="form.kiosk_dark" />
          <span>Dark mode</span>
        </label>
        <label class="checkbox-row ts-field">
          <input type="checkbox" v-model="form.kiosk_menu" />
          <span>Allow right-click / refresh menu</span>
        </label>
      </template>
      <label v-if="urlCapable" class="checkbox-row">
        <input type="checkbox" v-model="form.ephemeral" />
        <span>Ephemeral (no saved data — wiped when halted)</span>
      </label>

      <WorkspaceOptionsFields :form="form" :lan-policy="lanPolicy" :gluetun-ready="gluetunReady" :gpu-enabled="gpuPolicy.enabled" />

      <p class="apply-note">Changes apply the next time the workspace boots.</p>

      <div v-if="error" class="form-error">{{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading">Save</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { reactive, computed, ref, watch, onMounted } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import WorkspaceOptionsFields from './WorkspaceOptionsFields.vue'
import { workspacesApi } from '@/api/workspaces'
import { usersApi } from '@/api/users'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import type { GpuPolicy, LanPolicy, Workspace } from '@/types'

const props = defineProps<{ ws: Workspace }>()
const open = defineModel<boolean>({ default: false })

const loading = ref(false)
const error = ref('')
const lanPolicy = ref<LanPolicy>({ enabled: false, subnets: [] })
const gpuPolicy = ref<GpuPolicy>({ enabled: false })
const gluetunReady = ref(false)
const store = useWorkspacesStore()
const ui = useUiStore()

const urlCapable = computed(
  () => props.ws.workspace_type === 'browser' || props.ws.workspace_type === 'link',
)
const urlCount = computed(() => form.target_url.trim().split(/\s+/).filter(Boolean).length)

const form = reactive({
  name: '',
  target_url: '',
  kiosk: false,
  kiosk_dark: false,
  kiosk_menu: false,
  ephemeral: false,
  use_tailscale: false,
  use_gluetun: false,
  lan_access: false,
  ts_exit_node: '',
  ts_accept_routes: true,
  ts_accept_dns: true,
  custom_dns: false,
  dns_servers: '',
  allow_sudo: false,
  inject_ssh_key: true,
  pixelflux_wayland: true,
  clear_browser_lock: false,
  gpu_accel: false,
  install_packages: '',
  proot_apps: [] as string[],
  appimages: '',
})

// Stored proot_apps is a space/comma-separated string; the selector works on an array.
function parseProotApps(value: string | null): string[] {
  return value ? value.split(/[,\s]+/).filter(Boolean) : []
}

function resetFromWs() {
  form.name = props.ws.name
  form.target_url = props.ws.target_url ?? ''
  form.kiosk = props.ws.kiosk
  form.kiosk_dark = props.ws.kiosk_dark
  form.kiosk_menu = props.ws.kiosk_menu
  form.ephemeral = props.ws.ephemeral
  form.use_tailscale = props.ws.use_tailscale
  form.use_gluetun = props.ws.use_gluetun
  form.lan_access = props.ws.lan_access
  form.ts_exit_node = props.ws.ts_exit_node ?? ''
  form.ts_accept_routes = props.ws.ts_accept_routes
  form.ts_accept_dns = props.ws.ts_accept_dns
  form.custom_dns = props.ws.custom_dns
  form.dns_servers = props.ws.dns_servers ?? ''
  form.allow_sudo = props.ws.allow_sudo
  form.inject_ssh_key = props.ws.inject_ssh_key
  form.pixelflux_wayland = props.ws.pixelflux_wayland
  form.clear_browser_lock = props.ws.clear_browser_lock
  form.gpu_accel = props.ws.gpu_accel
  form.install_packages = props.ws.install_packages ?? ''
  form.proot_apps = parseProotApps(props.ws.proot_apps)
  form.appimages = props.ws.appimages ?? ''
}

async function loadLanPolicy() {
  try {
    lanPolicy.value = await workspacesApi.lanPolicy()
  } catch {
    // Non-fatal: the LAN checkbox just stays hidden if the policy can't load.
  }
  try {
    gpuPolicy.value = await workspacesApi.gpuPolicy()
  } catch {
    // Non-fatal: the GPU checkbox just stays hidden if the policy can't load.
  }
  try {
    const g = await usersApi.getGluetun()
    gluetunReady.value = g.enabled && g.has_config
  } catch {
    // Non-fatal: Gluetun toggle just stays hidden.
  }
}

// Re-seed the form from the workspace whenever the modal is opened.
watch(open, value => {
  if (value) {
    error.value = ''
    resetFromWs()
    loadLanPolicy()
  }
})

onMounted(() => {
  resetFromWs()
  loadLanPolicy()
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    await store.update(props.ws.id, {
      name: form.name,
      target_url: urlCapable.value ? form.target_url : undefined,
      kiosk: urlCapable.value ? (urlCount.value <= 1 ? form.kiosk : false) : undefined,
      kiosk_dark: urlCapable.value ? (urlCount.value <= 1 ? form.kiosk_dark : false) : undefined,
      kiosk_menu: urlCapable.value ? (urlCount.value <= 1 ? form.kiosk_menu : false) : undefined,
      ephemeral: urlCapable.value ? form.ephemeral : undefined,
      use_tailscale: form.use_tailscale,
      use_gluetun: form.use_gluetun,
      lan_access: form.lan_access,
      ...(form.use_tailscale
        ? {
            ts_exit_node: form.ts_exit_node || undefined,
            ts_accept_routes: form.ts_accept_routes,
            ts_accept_dns: form.ts_accept_dns,
            custom_dns: false,
          }
        : {
            custom_dns: form.custom_dns,
            dns_servers: form.custom_dns ? form.dns_servers.trim() : '',
          }),
      allow_sudo: form.allow_sudo,
      inject_ssh_key: form.inject_ssh_key,
      pixelflux_wayland: form.pixelflux_wayland,
      clear_browser_lock: form.clear_browser_lock,
      gpu_accel: form.gpu_accel,
      install_packages: form.install_packages.trim(),
      proot_apps: form.proot_apps.join(' '),
      appimages: form.appimages.trim(),
    })
    open.value = false
    ui.toast('Workspace updated', 'success')
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
.apply-note {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-muted);
  font-family: var(--font-mono);
  margin: 0;
}
</style>
