<template>
  <BaseModal v-model="open" title="Edit Workspace">
    <form @submit.prevent="handleSubmit" class="form">
      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="My Desktop" required />
      </div>

      <div v-if="urlCapable" class="form-group">
        <label>Target URL</label>
        <input v-model="form.target_url" type="url" placeholder="https://example.com" />
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
import ProotAppsSelect from './ProotAppsSelect.vue'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import type { Workspace } from '@/types'

const props = defineProps<{ ws: Workspace }>()
const open = defineModel<boolean>({ default: false })

const loading = ref(false)
const error = ref('')
const store = useWorkspacesStore()
const ui = useUiStore()

const urlCapable = computed(
  () => props.ws.workspace_type === 'browser' || props.ws.workspace_type === 'link',
)

const form = reactive({
  name: '',
  target_url: '',
  kiosk: false,
  kiosk_dark: false,
  kiosk_menu: false,
  use_tailscale: false,
  ts_exit_node: '',
  ts_accept_routes: true,
  ts_accept_dns: true,
  allow_sudo: false,
  install_packages: '',
  proot_apps: [] as string[],
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
  form.use_tailscale = props.ws.use_tailscale
  form.ts_exit_node = props.ws.ts_exit_node ?? ''
  form.ts_accept_routes = props.ws.ts_accept_routes
  form.ts_accept_dns = props.ws.ts_accept_dns
  form.allow_sudo = props.ws.allow_sudo
  form.install_packages = props.ws.install_packages ?? ''
  form.proot_apps = parseProotApps(props.ws.proot_apps)
}

// Re-seed the form from the workspace whenever the modal is opened.
watch(open, value => {
  if (value) {
    error.value = ''
    resetFromWs()
  }
})

onMounted(() => {
  resetFromWs()
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    await store.update(props.ws.id, {
      name: form.name,
      target_url: urlCapable.value ? form.target_url : undefined,
      kiosk: urlCapable.value ? form.kiosk : undefined,
      kiosk_dark: urlCapable.value ? form.kiosk_dark : undefined,
      kiosk_menu: urlCapable.value ? form.kiosk_menu : undefined,
      use_tailscale: form.use_tailscale,
      ...(form.use_tailscale
        ? {
            ts_exit_node: form.ts_exit_node || undefined,
            ts_accept_routes: form.ts_accept_routes,
            ts_accept_dns: form.ts_accept_dns,
          }
        : {}),
      allow_sudo: form.allow_sudo,
      install_packages: form.install_packages.trim(),
      proot_apps: form.proot_apps.join(' '),
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
.apply-note {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-muted);
  font-family: var(--font-mono);
  margin: 0;
}
</style>
