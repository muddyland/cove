<template>
  <AppShell>
    <div class="page-header">
      <h2>// SETTINGS</h2>
    </div>

    <div class="panels">
      <section class="panel">
        <h3>// SYSTEM</h3>
        <div v-if="loading" class="loading">Loading…</div>
        <form v-else class="form" @submit.prevent="handleSave">
          <div class="form-group">
            <label>// tailscale image</label>
            <input
              v-model="form.tailscale_image"
              type="text"
              placeholder="tailscale/tailscale:latest"
              autocomplete="off"
            />
            <p class="hint">
              Container image used for the Tailscale sidecar. Admins can pin a tag or digest —
              e.g. <code>tailscale/tailscale:v1.74.0</code> or
              <code>tailscale/tailscale@sha256:…</code> — for reproducible deployments.
            </p>
          </div>

          <label class="checkbox-row">
            <input type="checkbox" v-model="form.workspace_lan_access" />
            <span>Workspace LAN access</span>
          </label>
          <p class="hint">
            When <strong>off</strong> (default), workspaces are restricted to WAN only — private
            RFC1918 ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) are blocked. When
            <strong>on</strong>, workspaces may reach hosts on your local network.
          </p>

          <div v-if="error" class="form-error">⚠ {{ error }}</div>
          <div class="form-actions">
            <NeonButton type="submit" variant="primary" :loading="saving">Save Settings</NeonButton>
          </div>
        </form>
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const error = ref('')

const form = reactive({
  tailscale_image: '',
  workspace_lan_access: false,
})

onMounted(async () => {
  try {
    const settings = await adminApi.settings.get()
    form.tailscale_image = settings.tailscale_image
    form.workspace_lan_access = settings.workspace_lan_access
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function handleSave() {
  error.value = ''
  saving.value = true
  try {
    const updated = await adminApi.settings.update({
      tailscale_image: form.tailscale_image,
      workspace_lan_access: form.workspace_lan_access,
    })
    form.tailscale_image = updated.tailscale_image
    form.workspace_lan_access = updated.workspace_lan_access
    ui.toast('Settings saved', 'success')
  } catch (e: any) {
    error.value = e.message
    ui.toast(e.message, 'error')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
@import '@/styles/tables.css';

.panels { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  width: 520px;
  max-width: 100%;
}
.panel h3 {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--accent);
  text-shadow: var(--glow-sm);
  margin-bottom: 18px;
}
.form { display: flex; flex-direction: column; gap: 14px; }
.form-actions { display: flex; justify-content: flex-end; }
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
  margin-bottom: 0;
}
.checkbox-row input { width: auto; margin: 0; }
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
.loading { color: var(--text-muted); font-family: var(--font-mono); font-size: 12px; }
</style>
