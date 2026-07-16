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
        <input type="checkbox" :checked="form.use_tailscale" @change="pickTailscale($event)" />
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

      <template v-if="gluetunReady || form.use_gluetun">
        <label class="checkbox-row">
          <input type="checkbox" :checked="form.use_gluetun" @change="pickGluetun($event)" />
          <span>Route through Gluetun (VPN)</span>
        </label>
        <p v-if="form.use_gluetun" class="hint ts-field">
          Uses your Gluetun VPN config (set it in Preferences → Gluetun). All egress
          goes through the VPN tunnel.
        </p>
      </template>

      <template v-if="!form.use_tailscale && !form.use_gluetun">
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

      <label class="checkbox-row">
        <input type="checkbox" v-model="form.inject_ssh_key" />
        <span>Inject my SSH key</span>
      </label>
      <p class="hint">
        Copy your account SSH key into the container's <code>~/.ssh</code> at launch
        (set it in Preferences → SSH key). Turn off to keep this workspace key-free.
      </p>

      <label class="checkbox-row">
        <input type="checkbox" v-model="form.pixelflux_wayland" />
        <span>Wayland streaming</span>
      </label>
      <p class="hint">
        Stream the desktop over Wayland (default). Turn off to force the X11 fallback
        (<code>PIXELFLUX_WAYLAND=false</code>) if an app has Wayland compatibility issues.
      </p>

      <label class="checkbox-row">
        <input type="checkbox" v-model="form.clear_browser_lock" />
        <span>Clear stale browser lock</span>
      </label>
      <p class="hint">
        For browser workspaces: remove a leftover single-instance lock from the saved
        profile at launch. Enable if the browser won't start after an unclean halt
        (the desktop streams but the browser never appears).
      </p>

      <template v-if="gpuEnabled">
        <label class="checkbox-row">
          <input type="checkbox" v-model="form.gpu_accel" />
          <span>GPU acceleration</span>
        </label>
        <p class="hint">
          Use the host GPU for hardware video encode (VAAPI), offloading the stream
          from the CPU for smoother, lower-latency desktops. Best with Wayland
          streaming on (hardware encode needs it). Requires a GPU on the workspace's
          host.
        </p>
      </template>

      <template v-if="dockerEnabled">
        <label class="checkbox-row">
          <input type="checkbox" v-model="form.use_docker" />
          <span>Docker (dev)</span>
        </label>
        <p class="hint">
          Run <code>docker</code> inside this workspace (build/run containers) via a
          per-workspace nested daemon. Runs a <strong>privileged</strong> sidecar —
          it can't reach the host Docker or other workspaces, but grant it only to
          trusted users. Nested images and state are discarded when the workspace is
          halted.
        </p>
      </template>

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
  use_gluetun: boolean
  lan_access: boolean
  ts_exit_node: string
  ts_accept_routes: boolean
  ts_accept_dns: boolean
  custom_dns: boolean
  dns_servers: string
  allow_sudo: boolean
  inject_ssh_key: boolean
  pixelflux_wayland: boolean
  clear_browser_lock: boolean
  gpu_accel: boolean
  use_docker: boolean
  install_packages: string
  proot_apps: string[]
  appimages: string
}

const props = defineProps<{
  form: WorkspaceOptionsForm
  lanPolicy: LanPolicy
  // Only offer the Gluetun toggle when the user has a usable VPN profile.
  gluetunReady?: boolean
  // Only offer the GPU toggle when an admin has enabled acceleration.
  gpuEnabled?: boolean
  // Only offer the Docker (dev) toggle when an admin has enabled Docker-in-Docker.
  dockerEnabled?: boolean
}>()

const networkSummary = computed(() => {
  const parts: string[] = []
  parts.push(
    props.form.use_tailscale
      ? 'Tailscale'
      : props.form.use_gluetun
        ? 'Gluetun VPN'
        : props.form.custom_dns
          ? 'Custom DNS'
          : 'Direct',
  )
  if (props.form.lan_access && props.lanPolicy.enabled && props.lanPolicy.subnets.length) {
    parts.push('+ LAN')
  }
  return parts.join(' ')
})

// Tailscale and Gluetun are mutually exclusive routing modes — selecting one
// clears the other.
function pickTailscale(e: Event) {
  const on = (e.target as HTMLInputElement).checked
  props.form.use_tailscale = on
  if (on) props.form.use_gluetun = false
}
function pickGluetun(e: Event) {
  const on = (e.target as HTMLInputElement).checked
  props.form.use_gluetun = on
  if (on) props.form.use_tailscale = false
}

const appsSummary = computed(() => {
  const n =
    (props.form.install_packages.trim() ? 1 : 0) +
    props.form.proot_apps.length +
    props.form.appimages.split(/\n+/).filter(s => s.trim()).length
  const bits: string[] = []
  if (n) bits.push(`${n} app${n === 1 ? '' : 's'}`)
  if (props.form.allow_sudo) bits.push('sudo')
  if (props.form.inject_ssh_key) bits.push('ssh key')
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
