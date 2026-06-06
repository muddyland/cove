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
      <div v-if="urlCapable" class="form-group">
        <label>{{ urlRequired ? 'Target URL' : 'Open URL (optional)' }}</label>
        <input
          v-model="form.target_url"
          type="url"
          placeholder="https://example.com"
          :required="urlRequired"
        />
      </div>
      <label v-if="urlCapable" class="checkbox-row">
        <input type="checkbox" v-model="form.kiosk" />
        <span>Kiosk mode (full-screen, no browser chrome)</span>
      </label>
      <template v-if="urlCapable && form.kiosk">
        <label class="checkbox-row ts-field">
          <input type="checkbox" v-model="form.kiosk_dark" />
          <span>Dark mode</span>
        </label>
        <label class="checkbox-row ts-field">
          <input type="checkbox" v-model="form.kiosk_menu" />
          <span>Allow right-click / refresh menu</span>
        </label>
      </template>
      <label class="checkbox-row">
        <input type="checkbox" v-model="form.use_tailscale" />
        <span>Route through Tailscale</span>
      </label>
      <template v-if="form.use_tailscale">
        <div class="form-group ts-field">
          <label>Exit node (optional)</label>
          <input v-model="form.ts_exit_node" type="text" placeholder="us-nyc-1 or 100.x.y.z" />
        </div>
        <label class="checkbox-row ts-field">
          <input type="checkbox" v-model="form.ts_accept_routes" />
          <span>Accept routes</span>
        </label>
        <label class="checkbox-row ts-field">
          <input type="checkbox" v-model="form.ts_accept_dns" />
          <span>Accept DNS</span>
        </label>
      </template>

      <template v-if="!form.use_tailscale">
        <label class="checkbox-row">
          <input type="checkbox" v-model="form.custom_dns" />
          <span>Use custom DNS (public resolvers)</span>
        </label>
        <div v-if="form.custom_dns" class="form-group ts-field">
          <label>DNS servers</label>
          <input v-model="form.dns_servers" type="text" placeholder="1.1.1.1 9.9.9.9" />
          <div class="dns-presets">
            <button type="button" @click="addDns('1.1.1.1')">+ Cloudflare</button>
            <button type="button" @click="addDns('9.9.9.9')">+ Quad9</button>
            <button type="button" @click="addDns('8.8.8.8')">+ Google</button>
          </div>
          <p class="hint">Space/comma separated IPs. Leave empty to use 1.1.1.1 + 9.9.9.9.</p>
        </div>
      </template>

      <details class="advanced">
        <summary>Advanced</summary>
        <div class="advanced-body">
          <label class="checkbox-row">
            <input type="checkbox" v-model="form.allow_sudo" />
            <span>Allow sudo</span>
          </label>
          <p class="hint">
            Allow in-container <code>sudo</code>. Admins can force-disable sudo globally in
            Settings, which overrides this choice.
          </p>

          <div class="form-group">
            <label>Install packages</label>
            <input v-model="form.install_packages" type="text" placeholder="git vim htop" />
            <p class="hint">
              Distro packages installed at launch via the LinuxServer
              <code>universal-package-install</code> mod.
            </p>
          </div>

          <div class="form-group">
            <label>proot-apps</label>
            <ProotAppsSelect v-model="form.proot_apps" />
            <p class="hint">
              Portable apps via LinuxServer <code>proot-apps</code> (desktop images).
              Select one or more.
            </p>
          </div>
        </div>
      </details>

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
import ProotAppsSelect from './ProotAppsSelect.vue'
import { imagesApi } from '@/api/images'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type { WorkspaceImage } from '@/types'

const open = defineModel<boolean>({ default: false })

const images = ref<WorkspaceImage[]>([])
const loading = ref(false)
const error = ref('')
const store = useWorkspacesStore()
const ui = useUiStore()
const router = useRouter()

const form = reactive({
  name: '',
  image_id: '' as number | '',
  target_url: '',
  kiosk: false,
  kiosk_dark: false,
  kiosk_menu: false,
  use_tailscale: false,
  ts_exit_node: '',
  ts_accept_routes: true,
  ts_accept_dns: true,
  custom_dns: false,
  dns_servers: '',
  allow_sudo: false,
  install_packages: '',
  proot_apps: [] as string[],
})

function addDns(ip: string) {
  const list = form.dns_servers.split(/[,\s]+/).filter(Boolean)
  if (!list.includes(ip)) list.push(ip)
  form.dns_servers = list.join(' ')
}

const selectedImage = computed(() => images.value.find(i => i.id === form.image_id))
const urlCapable = computed(() =>
  !!selectedImage.value && (!!selectedImage.value.url_env || selectedImage.value.image_type === 'link'),
)
const urlRequired = computed(() => selectedImage.value?.image_type === 'link')

onMounted(async () => {
  images.value = await imagesApi.list()
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    const ws = await store.launch({
      name: form.name,
      image_id: form.image_id as number,
      workspace_type: selectedImage.value?.image_type ?? 'desktop',
      target_url: urlCapable.value && form.target_url ? form.target_url : undefined,
      kiosk: urlCapable.value ? form.kiosk : false,
      kiosk_dark: urlCapable.value && form.kiosk ? form.kiosk_dark : false,
      kiosk_menu: urlCapable.value && form.kiosk ? form.kiosk_menu : false,
      use_tailscale: form.use_tailscale,
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
      ...(form.install_packages.trim() ? { install_packages: form.install_packages.trim() } : {}),
      ...(form.proot_apps.length ? { proot_apps: form.proot_apps.join(' ') } : {}),
    })
    open.value = false
    ui.toast(`Launching ${form.name}…`, 'info')
    form.name = ''
    form.image_id = ''
    form.target_url = ''
    form.kiosk = false
    form.kiosk_dark = false
    form.kiosk_menu = false
    form.use_tailscale = false
    form.ts_exit_node = ''
    form.ts_accept_routes = true
    form.ts_accept_dns = true
    form.custom_dns = false
    form.dns_servers = ''
    form.allow_sudo = false
    form.install_packages = ''
    form.proot_apps = []
    router.push(`/workspace/${ws.id}`)
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
.advanced {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 14px;
}
.advanced > summary {
  cursor: pointer;
  font-size: 12px;
  letter-spacing: 0.5px;
  color: var(--text);
  user-select: none;
}
.advanced-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 16px;
}
.hint {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-muted);
  margin: 4px 0 0;
}
.hint code {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--accent);
}
.dns-presets { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.dns-presets button {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.5px;
  padding: 3px 8px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.dns-presets button:hover { color: var(--accent); border-color: var(--accent); }
</style>
