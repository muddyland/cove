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
        :title="!tailscaleReady && !form.use_tailscale ? 'Add a Tailscale profile in Preferences to enable' : ''"
        :aria-describedby="!tailscaleReady && !form.use_tailscale ? 'net-unavailable' : undefined"
        @click="setMode('tailscale')"
      >
        <svg class="seg-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="TAILSCALE_ICON" /></svg>
        <span>Tailscale</span>
      </button>
      <button
        type="button" role="tab" :aria-selected="mode === 'gluetun'"
        :class="{ on: mode === 'gluetun' }"
        :disabled="!gluetunReady && !form.use_gluetun"
        :title="!gluetunReady && !form.use_gluetun ? 'Add a VPN profile in Preferences to enable' : ''"
        :aria-describedby="!gluetunReady && !form.use_gluetun ? 'net-unavailable' : undefined"
        @click="setMode('gluetun')"
      >
        <svg class="seg-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="WIREGUARD_ICON" /></svg>
        <span>Gluetun</span>
      </button>
    </div>
    <!-- Say why a mode is greyed out in the page itself: a title tooltip never
         shows on touch screens and isn't read reliably by screen readers. -->
    <p v-if="unavailable.length" id="net-unavailable" class="hint unavailable">
      <Info :size="12" />
      <span>
        {{ unavailable.join(' and ') }} {{ unavailable.length > 1 ? 'are' : 'is' }} unavailable until you add
        {{ unavailableNeeds }} in <a href="/app/preferences" target="_blank" rel="noopener">Preferences</a> (opens in a new tab, so this form is kept).
      </span>
    </p>

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
        <label for="net-ts-profile">Tailscale profile</label>
        <select id="net-ts-profile" v-model="form.tailscale_profile_id">
          <option
            v-for="p in tsProfiles" :key="p.id" :value="p.id" :disabled="!!p.problem"
          >{{ p.name }}{{ p.login_server ? ` · ${host(p.login_server)}` : '' }}{{ p.problem ? ` — ${p.problem}` : '' }}</option>
        </select>
        <p class="hint">
          Which auth key / tailnet this workspace joins.
          <a href="/app/preferences#tailscale" target="_blank" rel="noopener">Manage profiles</a>
        </p>
      </div>
      <div class="form-group ts-field">
        <label>Exit node (optional)</label>
        <input v-model="form.ts_exit_node" type="text" placeholder="us-nyc-1 or 100.x.y.z" />
      </div>
      <div class="ts-field toggles">
        <ToggleRow v-model="form.ts_accept_routes">Accept routes</ToggleRow>
        <ToggleRow v-model="form.ts_accept_dns">Accept DNS</ToggleRow>
      </div>
      <p class="hint">Egress routes through the tailnet of the profile above.</p>
    </template>

    <!-- Gluetun -->
    <template v-else>
      <div class="form-group ts-field">
        <label for="net-vpn-profile">VPN profile</label>
        <select id="net-vpn-profile" v-model="form.gluetun_profile_id">
          <option
            v-for="p in vpnProfileList" :key="p.id" :value="p.id" :disabled="!!p.problem"
          >{{ p.name }} · {{ vpnSummary(p) }}{{ p.problem ? ` — ${p.problem}` : busyElsewhere(p) ? ` — connected in ${p.active_in}` : '' }}</option>
        </select>
        <p v-if="selectedVpn && busyElsewhere(selectedVpn)" class="hint warn">
          <AlertTriangle :size="12" />
          <span>
            “{{ selectedVpn.name }}” is connected in <strong>{{ selectedVpn.active_in }}</strong>. A VPN
            profile allows one connection at a time, so this workspace can't start until that one
            stops — or pick another profile.
          </span>
        </p>
        <p v-else class="hint">
          All egress goes through this VPN tunnel; nothing leaves if it drops.
          <a href="/app/preferences#vpn" target="_blank" rel="noopener">Manage profiles</a>
        </p>
      </div>
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
import { AlertTriangle, Globe, Info } from 'lucide-vue-next'
import ToggleRow from './ToggleRow.vue'
import type { LanPolicy, TailscaleProfile, VpnProfile } from '@/types'
import {
  CLOUDFLARE_ICON, GOOGLE_ICON, QUAD9_ICON, TAILSCALE_ICON, WIREGUARD_ICON,
} from '@/utils/brandIcons'


export interface NetworkForm {
  use_tailscale: boolean
  use_gluetun: boolean
  tailscale_profile_id: number | null
  gluetun_profile_id: number | null
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
  tailscaleProfiles?: TailscaleProfile[]
  vpnProfiles?: VpnProfile[]
  // The workspace being edited, so its own VPN connection isn't reported as
  // "connected elsewhere".
  selfName?: string
  // Validation is owned by the parent (see utils/workspaceForm) and passed in.
  dnsError?: string
}>()

const tsProfiles = computed(() => props.tailscaleProfiles ?? [])
const vpnProfileList = computed(() => props.vpnProfiles ?? [])
// A mode is usable once at least one of its profiles is complete.
const tailscaleReady = computed(() => tsProfiles.value.some(p => !p.problem))
const gluetunReady = computed(() => vpnProfileList.value.some(p => !p.problem))
const selectedVpn = computed(() =>
  vpnProfileList.value.find(p => p.id === props.form.gluetun_profile_id) ?? null,
)

function host(url: string) {
  try { return new URL(url).host } catch { return url }
}
function vpnSummary(p: VpnProfile) {
  const kind = p.vpn_type === 'wireguard' ? 'WireGuard' : 'OpenVPN'
  if (p.mode === 'provider') {
    const where = [p.server_countries, p.server_cities].filter(Boolean).join(' / ')
    return [p.provider_label ?? p.provider, kind, where].filter(Boolean).join(' · ')
  }
  return p.config_filename ? `${kind} · ${p.config_filename}` : kind
}
function busyElsewhere(p: VpnProfile) {
  return !!p.active_in && p.active_in !== props.selfName
}

// Modes greyed out because the user hasn't configured them yet.
const unavailable = computed(() => {
  const out: string[] = []
  if (!tailscaleReady.value && !props.form.use_tailscale) out.push('Tailscale')
  if (!gluetunReady.value && !props.form.use_gluetun) out.push('Gluetun')
  return out
})
const unavailableNeeds = computed(() =>
  unavailable.value
    .map(m => (m === 'Tailscale' ? 'a Tailscale profile' : 'a VPN profile'))
    .join(' / '),
)

type Mode = 'direct' | 'tailscale' | 'gluetun'
const mode = computed<Mode>(() =>
  props.form.use_tailscale ? 'tailscale' : props.form.use_gluetun ? 'gluetun' : 'direct',
)

function setMode(m: Mode) {
  props.form.use_tailscale = m === 'tailscale'
  props.form.use_gluetun = m === 'gluetun'
  // Pre-select a usable profile (preferring a VPN one that's free right now).
  if (m === 'tailscale' && !tsProfiles.value.some(p => p.id === props.form.tailscale_profile_id && !p.problem)) {
    props.form.tailscale_profile_id = tsProfiles.value.find(p => !p.problem)?.id ?? null
  }
  if (m === 'gluetun' && !vpnProfileList.value.some(p => p.id === props.form.gluetun_profile_id && !p.problem)) {
    const ready = vpnProfileList.value.filter(p => !p.problem)
    props.form.gluetun_profile_id = (ready.find(p => !busyElsewhere(p)) ?? ready[0])?.id ?? null
  }
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
.hint { font-size: 12px; line-height: 1.5; color: var(--text-muted); margin: 4px 0 0; }
.hint code { font-family: var(--font-mono); font-size: 11px; color: var(--accent); }
.hint.unavailable { display: flex; gap: 6px; align-items: flex-start; margin-top: -8px; }
.hint.warn { display: flex; gap: 6px; align-items: flex-start; color: var(--amber); }
.hint.warn svg { flex-shrink: 0; margin-top: 3px; }
.hint a { color: var(--accent); }
.hint.unavailable svg { flex-shrink: 0; margin-top: 3px; }
.field-error { font-size: 12px; color: var(--red); margin: 4px 0 0; }
.dns-presets { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.dns-presets button {
  display: inline-flex; align-items: center; gap: 5px;
  background: transparent; border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text-muted); font-family: var(--font-mono); font-size: 11px;
  letter-spacing: 0.5px; padding: 3px 8px; cursor: pointer; transition: color 0.15s, border-color 0.15s;
}
.dns-presets button:hover { color: var(--accent); border-color: var(--accent); }
.dns-icon { width: 12px; height: 12px; flex: none; fill: currentColor; }
</style>
