<template>
  <!-- Network & DNS — collapsed by default so the modal stays compact. The
       summary shows the active mode at a glance without expanding. -->
  <details class="section">
    <summary>
      <span class="sec-title">Network &amp; DNS</span>
      <span class="sec-sub">{{ networkSummary }}</span>
    </summary>
    <div class="section-body">
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

      <!-- Direct LAN access — only offered when an admin has enabled it AND
           configured ranges. Tailnet-routed LAN works regardless of this. -->
      <template v-if="lanPolicy.enabled && lanPolicy.subnets.length">
        <label class="checkbox-row">
          <input type="checkbox" v-model="form.lan_access" />
          <span>Allow direct LAN access</span>
        </label>
        <p class="hint">
          Reach these LAN ranges directly over the bridge:
          <code>{{ lanPolicy.subnets.join(', ') }}</code>.
          <template v-if="form.use_tailscale"> Tailnet-routed access always works.</template>
        </p>
      </template>
    </div>
  </details>

  <!-- Apps & permissions -->
  <details class="section">
    <summary>
      <span class="sec-title">Apps &amp; permissions</span>
      <span class="sec-sub">{{ appsSummary }}</span>
    </summary>
    <div class="section-body">
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

      <div class="form-group">
        <label>AppImage apps</label>
        <textarea v-model="form.appimages" rows="2" placeholder="https://example.com/App.AppImage" />
        <p class="hint">
          One AppImage URL per line. Each is downloaded, extracted, and given a
          desktop launcher (Electron apps run with <code>--no-sandbox</code>).
        </p>
      </div>
    </div>
  </details>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ProotAppsSelect from './ProotAppsSelect.vue'
import type { LanPolicy } from '@/types'

// The subset of the launch/edit form this component owns. Both modals share the
// same field names, so a single reactive object is passed in and mutated here.
export interface WorkspaceOptionsForm {
  use_tailscale: boolean
  lan_access: boolean
  ts_exit_node: string
  ts_accept_routes: boolean
  ts_accept_dns: boolean
  custom_dns: boolean
  dns_servers: string
  allow_sudo: boolean
  install_packages: string
  proot_apps: string[]
  appimages: string
}

const props = defineProps<{ form: WorkspaceOptionsForm; lanPolicy: LanPolicy }>()

const networkSummary = computed(() => {
  const parts: string[] = []
  parts.push(props.form.use_tailscale ? 'Tailscale' : props.form.custom_dns ? 'Custom DNS' : 'Direct')
  if (props.form.lan_access && props.lanPolicy.enabled && props.lanPolicy.subnets.length) {
    parts.push('+ LAN')
  }
  return parts.join(' ')
})

const appsSummary = computed(() => {
  const n =
    (props.form.install_packages.trim() ? 1 : 0) +
    props.form.proot_apps.length +
    props.form.appimages.split(/\n+/).filter(s => s.trim()).length
  const bits: string[] = []
  if (n) bits.push(`${n} app${n === 1 ? '' : 's'}`)
  if (props.form.allow_sudo) bits.push('sudo')
  return bits.join(', ')
})

function addDns(ip: string) {
  const list = props.form.dns_servers.split(/[,\s]+/).filter(Boolean)
  if (!list.includes(ip)) list.push(ip)
  props.form.dns_servers = list.join(' ')
}
</script>

<style scoped>
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
}
.checkbox-row input { width: auto; margin: 0; }
.ts-field { padding-left: 24px; border-left: 1px solid var(--border); }

.section {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 14px;
}
.section > summary {
  cursor: pointer;
  font-size: 12px;
  letter-spacing: 0.5px;
  color: var(--text);
  user-select: none;
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.sec-title { font-weight: 600; }
.sec-sub {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-muted);
  letter-spacing: 0.5px;
  margin-left: auto;
}
.section-body {
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
