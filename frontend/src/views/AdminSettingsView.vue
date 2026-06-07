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

          <div class="form-group">
            <label>// max workspace runtime (hours)</label>
            <input
              v-model.number="form.workspace_max_runtime_hours"
              type="number"
              min="0"
              placeholder="24"
            />
            <p class="hint">
              Auto-stop running workspaces after this many hours. 0 = unlimited.
            </p>
          </div>

          <div class="form-group">
            <label>// default CPU limit (cores)</label>
            <input
              v-model.number="form.workspace_cpu_limit"
              type="number"
              min="0"
              step="0.5"
              placeholder="0"
            />
            <p class="hint">
              Max CPU cores each workspace container may use (fractions allowed, e.g.
              <code>1.5</code>). 0 = unlimited. Applies to newly started workspaces.
            </p>
          </div>

          <div class="form-group">
            <label>// default memory limit (MB)</label>
            <input
              v-model.number="form.workspace_memory_limit_mb"
              type="number"
              min="0"
              step="256"
              placeholder="0"
            />
            <p class="hint">
              Max RAM each workspace container may use, in MB (e.g. <code>4096</code> = 4 GB).
              0 = unlimited. Applies to newly started workspaces.
            </p>
          </div>

          <label class="checkbox-row">
            <input type="checkbox" v-model="form.workspace_lan_access" />
            <span>Allow direct LAN access (opt-in per workspace)</span>
          </label>
          <p class="hint">
            Master switch for letting a workspace reach your LAN directly over the bridge. When
            <strong>off</strong> (default), workspaces are WAN-only. When <strong>on</strong>, a
            workspace may reach the ranges below <em>only if</em> its own "Allow direct LAN access"
            box is also ticked. The Docker-internal range (172.16.0.0/12) and cloud metadata
            (169.254.0.0/16) are <strong>always</strong> blocked, so workspaces can never reach the
            Cove backend, the socket proxy, or each other. Tailscale tailnet/subnet-routed access is
            independent of this.
          </p>
          <div v-if="form.workspace_lan_access" class="form-group">
            <label>// allowed LAN subnets</label>
            <input
              v-model="form.workspace_lan_subnets"
              type="text"
              placeholder="10.12.0.0/24, 192.168.1.0/24"
              autocomplete="off"
            />
            <p class="hint">
              Comma/space separated IPv4 CIDRs a workspace may reach directly. Bare IPs become
              <code>/32</code>; invalid entries are dropped on save. Leave empty to grant nothing.
            </p>
          </div>

          <label class="checkbox-row">
            <input type="checkbox" v-model="form.workspace_no_new_privileges" />
            <span>Force-disable workspace sudo</span>
          </label>
          <p class="hint">
            Global hardening override. When <strong>on</strong>, in-container
            <code>sudo</code>/setuid is force-disabled for <strong>all</strong> workspaces,
            overriding each workspace's per-launch "Allow sudo" choice. Leave
            <strong>off</strong> (default) to let users decide per workspace — desktop images
            such as Kali or webtop typically need <code>sudo</code>.
          </p>

          <div v-if="error" class="form-error">⚠ {{ error }}</div>
          <div class="form-actions">
            <NeonButton type="submit" variant="primary" :loading="saving">Save Settings</NeonButton>
          </div>
        </form>
      </section>

      <section class="panel">
        <h3>// ENVIRONMENT</h3>
        <p class="hint env-note">
          Read-only summary of configuration derived from environment variables. To change these,
          update the environment and restart the server. Secrets are masked.
        </p>
        <div v-if="envLoading" class="loading">Loading…</div>
        <div v-else-if="envError" class="form-error">⚠ {{ envError }}</div>
        <div v-else-if="envEntries.length === 0" class="empty">NO ENVIRONMENT CONFIG</div>
        <div v-else class="table-wrap">
          <table class="env-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="entry in envEntries" :key="entry.name">
                <td class="env-name">{{ entry.name }}</td>
                <td class="env-value">{{ entry.value }}</td>
              </tr>
            </tbody>
          </table>
        </div>
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
import type { EnvEntry } from '@/types'

const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const error = ref('')

const envLoading = ref(true)
const envError = ref('')
const envEntries = ref<EnvEntry[]>([])

const form = reactive({
  tailscale_image: '',
  workspace_lan_access: false,
  workspace_lan_subnets: '',
  workspace_no_new_privileges: false,
  workspace_max_runtime_hours: 24,
  workspace_cpu_limit: 0,
  workspace_memory_limit_mb: 0,
})

onMounted(async () => {
  try {
    const settings = await adminApi.settings.get()
    form.tailscale_image = settings.tailscale_image
    form.workspace_lan_access = settings.workspace_lan_access
    form.workspace_lan_subnets = settings.workspace_lan_subnets
    form.workspace_no_new_privileges = settings.workspace_no_new_privileges
    form.workspace_max_runtime_hours = settings.workspace_max_runtime_hours
    form.workspace_cpu_limit = settings.workspace_cpu_limit
    form.workspace_memory_limit_mb = settings.workspace_memory_limit_mb
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }

  try {
    const summary = await adminApi.env.get()
    envEntries.value = summary.entries
  } catch (e: any) {
    envError.value = e.message
  } finally {
    envLoading.value = false
  }
})

async function handleSave() {
  error.value = ''
  saving.value = true
  try {
    const updated = await adminApi.settings.update({
      tailscale_image: form.tailscale_image,
      workspace_lan_access: form.workspace_lan_access,
      workspace_lan_subnets: form.workspace_lan_subnets,
      workspace_no_new_privileges: form.workspace_no_new_privileges,
      workspace_max_runtime_hours: form.workspace_max_runtime_hours,
      workspace_cpu_limit: form.workspace_cpu_limit,
      workspace_memory_limit_mb: form.workspace_memory_limit_mb,
    })
    form.tailscale_image = updated.tailscale_image
    form.workspace_lan_access = updated.workspace_lan_access
    form.workspace_lan_subnets = updated.workspace_lan_subnets
    form.workspace_no_new_privileges = updated.workspace_no_new_privileges
    form.workspace_max_runtime_hours = updated.workspace_max_runtime_hours
    form.workspace_cpu_limit = updated.workspace_cpu_limit
    form.workspace_memory_limit_mb = updated.workspace_memory_limit_mb
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

.env-note { margin: 0 0 16px; }
.env-table td { font-family: var(--font-mono); font-size: 12px; vertical-align: top; }
.env-name { color: var(--text-muted); white-space: nowrap; }
.env-value { color: var(--text); word-break: break-all; }
.env-table .empty { padding: 24px; }
</style>
