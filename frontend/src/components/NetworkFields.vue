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
      <label class="checkbox-row">
        <input type="checkbox" v-model="form.custom_dns" />
        <span>Use custom DNS (public resolvers)</span>
      </label>
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
      <label class="checkbox-row ts-field">
        <input type="checkbox" v-model="form.ts_accept_routes" /><span>Accept routes</span>
      </label>
      <label class="checkbox-row ts-field">
        <input type="checkbox" v-model="form.ts_accept_dns" /><span>Accept DNS</span>
      </label>
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
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Globe } from 'lucide-vue-next'
import type { LanPolicy } from '@/types'

// Official brand marks, inlined (single-path, from Simple Icons — CC0) so they
// ship with the bundle: no CDN/external request, and they tint via currentColor.
const TAILSCALE_ICON =
  'M24 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm-9 9a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm0-9a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm6-6a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm0-.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM3 24a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm0-.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zm18 .5a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm0-.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM6 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm9-9a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm-3 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM6 3a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM3 5.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z'
const WIREGUARD_ICON =
  'M23.98 11.645S24.533 0 11.735 0C.418 0 .064 11.17.064 11.17S-1.6 24 11.997 24C25.04 24 23.98 11.645 23.98 11.645zM8.155 7.576c2.4-1.47 5.469-.571 6.618 1.638.218.419.246 1.063.108 1.503-.477 1.516-1.601 2.366-3.145 2.728.455-.39.817-.832.933-1.442a2.112 2.112 0 0 0-.364-1.677 2.14 2.14 0 0 0-2.465-.75c-.95.36-1.47 1.228-1.377 2.294.087.99.839 1.632 2.245 1.876-.21.111-.372.193-.53.281a5.113 5.113 0 0 0-1.644 1.43c-.143.192-.24.208-.458.075-2.827-1.729-3.009-6.067.078-7.956zM6.04 18.258c-.455.116-.895.286-1.359.438.227-1.532 2.021-2.943 3.539-2.782a3.91 3.91 0 0 0-.74 2.072c-.504.093-.98.155-1.44.272zM15.703 3.3c.448.017.898.01 1.347.02a2.324 2.324 0 0 1 .334.047 3.249 3.249 0 0 1-.34.434c-.16.15-.341.296-.573.069-.055-.055-.187-.042-.283-.044-.447-.005-.894-.02-1.34-.003a8.323 8.323 0 0 0-1.154.118c-.072.013-.178.25-.146.338.078.207.191.435.359.567.619.49 1.277.928 1.9 1.413.604.472 1.167.99 1.51 1.7.446.928.46 1.9.267 2.877-.322 1.63-1.147 2.98-2.483 3.962-.538.395-1.205.62-1.821.903-.543.25-1.1.465-1.644.712-.98.446-1.53 1.51-1.369 2.615.149 1.015 1.04 1.862 2.059 2.037 1.223.21 2.486-.586 2.785-1.83.336-1.397-.423-2.646-1.845-3.024l-.256-.066c.38-.17.708-.291 1.012-.458q.793-.437 1.558-.925c.15-.096.231-.096.36.014.977.846 1.56 1.898 1.724 3.187.27 2.135-.74 4.096-2.646 5.101-2.948 1.555-6.557-.215-7.208-3.484-.558-2.8 1.418-5.34 3.797-5.83 1.023-.211 1.958-.637 2.685-1.425.47-.508.697-.944.775-1.141a3.165 3.165 0 0 0 .217-1.158 2.71 2.71 0 0 0-.237-.992c-.248-.566-1.2-1.466-1.435-1.656l-2.24-1.754c-.079-.065-.168-.06-.36-.047-.23.016-.815.048-1.067-.018.204-.155.76-.38 1-.56-.726-.49-1.554-.314-2.315-.46.176-.328 1.046-.831 1.541-.888a7.323 7.323 0 0 0-.135-.822c-.03-.111-.154-.22-.263-.283-.262-.154-.541-.281-.843-.434a1.755 1.755 0 0 1 .906-.28 3.385 3.385 0 0 1 .908.088c.54.123.97.042 1.399-.324-.338-.136-.676-.26-1.003-.407a9.843 9.843 0 0 1-.942-.493c.85.118 1.671.437 2.54.32l.022-.118-2.018-.47c1.203-.11 2.323-.128 3.384.388.299.146.61.266.897.432.14.08.233.24.348.365.09.098.164.23.276.29.424.225.89.234 1.366.223l.01-.16c.479.15 1.017.702 1.017 1.105-.776 0-1.55-.003-2.325.004-.083 0-.165.061-.247.094.078.046.155.128.235.131zM14.703 2.153a.118.118 0 0 0-.016.19.179.179 0 0 0 .246.065c.075-.038.148-.078.238-.125-.072-.062-.13-.114-.19-.163-.106-.087-.193-.032-.278.033z'

// DNS-provider marks (Simple Icons, CC0), inlined + monochrome (currentColor).
const CLOUDFLARE_ICON =
  'M16.5088 16.8447c.1475-.5068.0908-.9707-.1553-1.3154-.2246-.3164-.6045-.499-1.0615-.5205l-8.6592-.1123a.1559.1559 0 0 1-.1333-.0713c-.0283-.042-.0351-.0986-.021-.1553.0278-.084.1123-.1484.2036-.1562l8.7359-.1123c1.0351-.0489 2.1601-.8868 2.5537-1.9136l.499-1.3013c.0215-.0561.0293-.1128.0147-.168-.5625-2.5463-2.835-4.4453-5.5499-4.4453-2.5039 0-4.6284 1.6177-5.3876 3.8614-.4927-.3658-1.1187-.5625-1.794-.499-1.2026.119-2.1665 1.083-2.2861 2.2856-.0283.31-.0069.6128.0635.894C1.5683 13.171 0 14.7754 0 16.752c0 .1748.0142.3515.0352.5273.0141.083.0844.1475.1689.1475h15.9814c.0909 0 .1758-.0645.2032-.1553l.12-.4268zm2.7568-5.5634c-.0771 0-.1611 0-.2383.0112-.0566 0-.1054.0415-.127.0976l-.3378 1.1744c-.1475.5068-.0918.9707.1543 1.3164.2256.3164.6055.498 1.0625.5195l1.8437.1133c.0557 0 .1055.0263.1329.0703.0283.043.0351.1074.0214.1562-.0283.084-.1132.1485-.204.1553l-1.921.1123c-1.041.0488-2.1582.8867-2.5527 1.914l-.1406.3585c-.0283.0713.0215.1416.0986.1416h6.5977c.0771 0 .1474-.0489.169-.126.1122-.4082.1757-.837.1757-1.2803 0-2.6025-2.125-4.727-4.7344-4.727'
const GOOGLE_ICON =
  'M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-7.667 0-.76-.053-1.467-.173-2.053H12.48z'
const QUAD9_ICON =
  'M6.822 24h5.608l6.331-9.48c1.463-2.185 2.288-4.197 2.288-6.4C21.05 3.458 17.144 0 12 0 6.822 0 2.95 3.493 2.95 8.207c0 4.507 3.459 8 8.345 8 .413 0 .757-.018 1.083-.07zM12 12.129c-2.426 0-4.215-1.634-4.215-3.957 0-2.34 1.79-3.957 4.215-3.957 2.409 0 4.215 1.617 4.215 3.957 0 2.323-1.806 3.957-4.215 3.957z'

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
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
}
.checkbox-row input { width: auto; margin: 0; }
.ts-field { padding-left: 24px; border-left: 1px solid var(--border); }
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
