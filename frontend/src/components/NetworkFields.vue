<template>
  <div class="net-fields">
    <div class="field-label">Network mode</div>
    <!-- One segmented choice instead of separate toggles: the three modes are
         mutually exclusive, so picking one is unambiguous (no silent unchecking). -->
    <div class="segmented" role="tablist" aria-label="Network mode">
      <button
        type="button" role="tab" :aria-selected="mode === 'direct'"
        :class="{ on: mode === 'direct' }" @click="setMode('direct')"
      >
        <Globe class="seg-icon" :size="14" aria-hidden="true" />
        <span>Direct</span>
      </button>
      <button
        type="button" role="tab" :aria-selected="mode === 'tailscale'"
        :class="{ on: mode === 'tailscale' }"
        :disabled="!tailscaleReady && !form.use_tailscale"
        :title="!tailscaleReady && !form.use_tailscale ? 'Add a Tailscale pre-auth key in Preferences to enable' : ''"
        @click="setMode('tailscale')"
      >
        <svg class="seg-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="TAILSCALE_ICON" /></svg>
        <span>Tailscale</span>
      </button>
      <button
        type="button" role="tab" :aria-selected="mode === 'gluetun'"
        :class="{ on: mode === 'gluetun' }"
        :disabled="!gluetunReady && !form.use_gluetun"
        :title="!gluetunReady && !form.use_gluetun ? 'Add a Gluetun VPN config in Preferences to enable' : ''"
        @click="setMode('gluetun')"
      >
        <svg class="seg-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="WIREGUARD_ICON" /></svg>
        <span>Gluetun</span>
      </button>
    </div>

    <!-- Direct -->
    <template v-if="mode === 'direct'">
      <ToggleRow v-model="form.custom_dns">Use custom DNS (public resolvers)</ToggleRow>
      <div v-if="form.custom_dns" class="form-group ts-field">
        <label>DNS servers</label>
        <input v-model="form.dns_servers" type="text" placeholder="1.1.1.1 9.9.9.9" />
        <div class="dns-presets">
          <button type="button" @click="addDns('1.1.1.1')">
            <svg class="dns-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="CLOUDFLARE_ICON" /></svg>Cloudflare
          </button>
          <button type="button" @click="addDns('9.9.9.9')">
            <svg class="dns-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="QUAD9_ICON" /></svg>Quad9
          </button>
          <button type="button" @click="addDns('8.8.8.8')">
            <svg class="dns-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="GOOGLE_ICON" /></svg>Google
          </button>
        </div>
        <p v-if="dnsError" class="field-error">{{ dnsError }}</p>
        <p v-else class="hint">Space/comma separated IPs. Leave empty to use 1.1.1.1 + 9.9.9.9.</p>
      </div>
      <p v-else class="hint">Standard bridge networking with the host's default resolver.</p>
    </template>

    <!-- Tailscale -->
    <template v-else-if="mode === 'tailscale'">
      <div class="form-group ts-field">
        <label>Exit node (optional)</label>
        <input v-model="form.ts_exit_node" type="text" placeholder="us-nyc-1 or 100.x.y.z" />
      </div>
      <div class="ts-field toggles">
        <ToggleRow v-model="form.ts_accept_routes">Accept routes</ToggleRow>
        <ToggleRow v-model="form.ts_accept_dns">Accept DNS</ToggleRow>
      </div>
      <p class="hint">
        Egress routes through your tailnet (set the auth key in Preferences → Tailscale).
        A workspace launch fails if Tailscale isn't configured.
      </p>
    </template>

    <!-- Gluetun -->
    <template v-else>
      <p class="hint">
        All egress goes through your Gluetun VPN tunnel (config in Preferences → Gluetun).
      </p>
    </template>

    <!-- Direct LAN access — orthogonal to the mode; only offered when an admin
         enabled it AND configured ranges. -->
    <template v-if="lanPolicy.enabled && lanPolicy.subnets.length">
      <ToggleRow v-model="form.lan_access">Allow direct LAN access</ToggleRow>
      <p class="hint">
        Reach these LAN ranges directly over the bridge:
        <code>{{ lanPolicy.subnets.join(', ') }}</code>.
        <template v-if="form.use_tailscale"> Tailnet-routed access always works.</template>
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Globe } from 'lucide-vue-next'
import ToggleRow from './ToggleRow.vue'
import type { LanPolicy } from '@/types'
import {
  CLOUDFLARE_ICON, GOOGLE_ICON, QUAD9_ICON, TAILSCALE_ICON, WIREGUARD_ICON,
} from '@/utils/brandIcons'


export interface NetworkForm {
  use_tailscale: boolean
  use_gluetun: boolean
  lan_access: boolean
  ts_exit_node: string
  ts_accept_routes: boolean
  ts_accept_dns: boolean
  custom_dns: boolean
  dns_servers: string
}

const props = defineProps<{
  form: NetworkForm
  lanPolicy: LanPolicy
  gluetunReady?: boolean
  tailscaleReady?: boolean
  // Validation is owned by the parent (see utils/workspaceForm) and passed in.
  dnsError?: string
}>()

type Mode = 'direct' | 'tailscale' | 'gluetun'
const mode = computed<Mode>(() =>
  props.form.use_tailscale ? 'tailscale' : props.form.use_gluetun ? 'gluetun' : 'direct',
)

function setMode(m: Mode) {
  props.form.use_tailscale = m === 'tailscale'
  props.form.use_gluetun = m === 'gluetun'
  // Custom DNS only applies to Direct networking.
  if (m !== 'direct') props.form.custom_dns = false
}

function addDns(ip: string) {
  const list = props.form.dns_servers.split(/[,\s]+/).filter(Boolean)
  if (!list.includes(ip)) list.push(ip)
  props.form.dns_servers = list.join(' ')
}
</script>

<style scoped>
.net-fields { display: flex; flex-direction: column; gap: 16px; }
.field-label {
  font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted);
}
.segmented { display: flex; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.segmented button {
  flex: 1; background: none; border: none; border-right: 1px solid var(--border);
  color: var(--text-muted); font-size: 12px; padding: 8px 6px; cursor: pointer; transition: all 0.15s;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
}
.segmented button:last-child { border-right: none; }
.segmented button.on { background: var(--accent); color: #06060f; }
.segmented button:disabled { opacity: 0.4; cursor: not-allowed; }
.seg-icon { width: 14px; height: 14px; flex: none; fill: currentColor; }
.ts-field { padding-left: 24px; border-left: 1px solid var(--border); }
.toggles { display: flex; flex-direction: column; gap: 8px; }
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 4px 0 0; }
.hint code { font-family: var(--font-mono); font-size: 10px; color: var(--accent); }
.field-error { font-size: 11px; color: var(--red); margin: 4px 0 0; }
.dns-presets { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.dns-presets button {
  display: inline-flex; align-items: center; gap: 5px;
  background: transparent; border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text-muted); font-family: var(--font-mono); font-size: 10px;
  letter-spacing: 0.5px; padding: 3px 8px; cursor: pointer; transition: color 0.15s, border-color 0.15s;
}
.dns-presets button:hover { color: var(--accent); border-color: var(--accent); }
.dns-icon { width: 12px; height: 12px; flex: none; fill: currentColor; }
</style>
