<template>
  <BaseModal v-model="open" title="Launch Workspace">
    <form @submit.prevent="handleSubmit" class="form">
      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="My Desktop" required />
      </div>
      <div class="form-group">
        <label>Image</label>
        <select v-model="form.image_id" required>
          <option value="" disabled>Select an image…</option>
          <option v-for="img in images" :key="img.id" :value="img.id">
            {{ img.name }} ({{ img.image_type }})
          </option>
        </select>
      </div>

      <div v-if="zonesStore.hasRemote" class="form-group">
        <label>Zone</label>
        <select v-model.number="form.zone_id">
          <option v-for="z in zonesStore.items" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <p class="hint">Which node runs this workspace. Local is this Cove host.</p>
      </div>
      <div v-if="urlCapable" class="form-group">
        <label>{{ urlRequired ? 'Target URL(s)' : 'Open URL(s) (optional)' }}</label>
        <textarea
          v-model="form.target_url"
          rows="2"
          placeholder="https://example.com"
          :required="urlRequired"
        />
        <p class="hint">One URL per line — each opens in its own tab (up to 6).</p>
      </div>
      <label v-if="urlCapable && urlCount <= 1" class="checkbox-row">
        <input type="checkbox" v-model="form.kiosk" />
        <span>Kiosk mode (full-screen, no browser chrome)</span>
      </label>
      <p v-if="urlCapable && urlCount > 1" class="hint">
        Multiple tabs open full-screen with a tab bar — kiosk lock is unavailable
        (it would hide the tabs).
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
      <template v-if="urlCapable">
        <label class="checkbox-row">
          <input type="checkbox" v-model="form.ephemeral" />
          <span>Ephemeral (no saved data — wiped when halted)</span>
        </label>
        <p v-if="form.ephemeral" class="hint ts-field">
          Cookies, history, and downloads live only in the container and are
          discarded on halt. Nothing is written to persistent storage.
        </p>
      </template>
      <WorkspaceOptionsFields :form="form" :lan-policy="lanPolicy" :gluetun-ready="gluetunReady" />

      <div v-if="error" class="form-error">{{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading">Launch</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import WorkspaceOptionsFields from './WorkspaceOptionsFields.vue'
import { imagesApi } from '@/api/images'
import { workspacesApi } from '@/api/workspaces'
import { usersApi } from '@/api/users'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useZonesStore } from '@/stores/zones'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type { LanPolicy, WorkspaceImage } from '@/types'

const open = defineModel<boolean>({ default: false })

const images = ref<WorkspaceImage[]>([])
const lanPolicy = ref<LanPolicy>({ enabled: false, subnets: [] })
const gluetunReady = ref(false)
const loading = ref(false)
const error = ref('')
const store = useWorkspacesStore()
const zonesStore = useZonesStore()
const ui = useUiStore()
const router = useRouter()

const form = reactive({
  name: '',
  image_id: '' as number | '',
  zone_id: 0 as number,
  target_url: '',
  kiosk: false,
  kiosk_dark: false,
  kiosk_menu: false,
  use_tailscale: false,
  use_gluetun: false,
  ephemeral: false,
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
  install_packages: '',
  proot_apps: [] as string[],
  appimages: '',
})

const selectedImage = computed(() => images.value.find(i => i.id === form.image_id))
const urlCapable = computed(() =>
  !!selectedImage.value && (!!selectedImage.value.url_env || selectedImage.value.image_type === 'link'),
)
const urlRequired = computed(() => selectedImage.value?.image_type === 'link')
const urlCount = computed(() => form.target_url.trim().split(/\s+/).filter(Boolean).length)

onMounted(async () => {
  zonesStore.fetch()
  images.value = await imagesApi.list()
  try {
    lanPolicy.value = await workspacesApi.lanPolicy()
  } catch {
    // Non-fatal: the LAN checkbox just stays hidden if the policy can't load.
  }
  try {
    const g = await usersApi.getGluetun()
    gluetunReady.value = g.enabled && g.has_config
  } catch {
    // Non-fatal: Gluetun toggle just stays hidden.
  }
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    const ws = await store.launch({
      name: form.name,
      image_id: form.image_id as number,
      workspace_type: selectedImage.value?.image_type ?? 'desktop',
      zone_id: form.zone_id,
      target_url: urlCapable.value && form.target_url ? form.target_url : undefined,
      kiosk: urlCapable.value && urlCount.value <= 1 ? form.kiosk : false,
      kiosk_dark: urlCapable.value && urlCount.value <= 1 && form.kiosk ? form.kiosk_dark : false,
      kiosk_menu: urlCapable.value && urlCount.value <= 1 && form.kiosk ? form.kiosk_menu : false,
      use_tailscale: form.use_tailscale,
      use_gluetun: form.use_gluetun,
      ephemeral: urlCapable.value ? form.ephemeral : false,
      lan_access: form.lan_access,
      ...(form.use_tailscale
        ? {
            ts_exit_node: form.ts_exit_node || undefined,
            ts_accept_routes: form.ts_accept_routes,
            ts_accept_dns: form.ts_accept_dns,
          }
        : {
            custom_dns: form.custom_dns,
            ...(form.custom_dns && form.dns_servers.trim()
              ? { dns_servers: form.dns_servers.trim() }
              : {}),
          }),
      allow_sudo: form.allow_sudo,
      inject_ssh_key: form.inject_ssh_key,
      pixelflux_wayland: form.pixelflux_wayland,
      clear_browser_lock: form.clear_browser_lock,
      ...(form.install_packages.trim() ? { install_packages: form.install_packages.trim() } : {}),
      ...(form.proot_apps.length ? { proot_apps: form.proot_apps.join(' ') } : {}),
      ...(form.appimages.trim() ? { appimages: form.appimages.trim() } : {}),
    })
    open.value = false
    ui.toast(`Launching ${form.name}…`, 'info')
    form.name = ''
    form.image_id = ''
    form.zone_id = 0
    form.target_url = ''
    form.kiosk = false
    form.kiosk_dark = false
    form.kiosk_menu = false
    form.ephemeral = false
    form.use_tailscale = false
    form.use_gluetun = false
    form.lan_access = false
    form.ts_exit_node = ''
    form.ts_accept_routes = true
    form.ts_accept_dns = true
    form.custom_dns = false
    form.dns_servers = ''
    form.allow_sudo = false
    form.inject_ssh_key = true
    form.pixelflux_wayland = true
    form.clear_browser_lock = false
    form.install_packages = ''
    form.proot_apps = []
    form.appimages = ''
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
