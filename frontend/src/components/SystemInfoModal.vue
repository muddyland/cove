<template>
  <BaseModal v-model="open" :title="`${ws.name} · System info`" width="760px">
    <LoadingSpinner v-if="!info && !error" block />
    <p v-else-if="!info" class="form-error" role="alert">⚠ {{ error }}</p>
    <div v-else class="sys">
      <!-- At a glance -->
      <div class="glance">
        <div class="g-item">
          <span class="g-label">Status</span>
          <StatusBadge :status="displayStatus(ws)" />
        </div>
        <div class="g-item">
          <span class="g-label">Uptime</span>
          <span class="g-val">{{ uptime ?? '—' }}</span>
        </div>
        <div class="g-item">
          <span class="g-label">Auto-stop</span>
          <span class="g-val">
            <template v-if="stopIn !== null">
              in {{ fmtDuration(stopIn) }}
              <button type="button" class="link-btn" :disabled="extending" @click="emit('extend')">Extend</button>
            </template>
            <template v-else>Off</template>
          </span>
        </div>
        <div class="g-item">
          <span class="g-label">Route</span>
          <span class="g-val route" :class="info.network.mode">
            <component :is="routeIcon" :size="13" /> {{ routeLabel }}
          </span>
        </div>
      </div>
      <p v-if="error" class="stale">⚠ Couldn't refresh ({{ error }}); showing the last reading.</p>

      <div class="grid">
        <!-- Resources -->
        <section class="card">
          <h4><Gauge :size="13" /> Resources</h4>
          <template v-if="r">
            <div class="metric">
              <div class="m-head">
                <span>CPU</span>
                <span class="m-val">
                  {{ pct(r.cpu_pct) }}
                  <span class="muted">of host{{ r.online_cpus ? ` (${r.online_cpus} cores)` : '' }}{{ c?.cpu_limit ? ` · capped at ${trimNum(c.cpu_limit)} cores` : '' }}</span>
                </span>
              </div>
              <div class="meter"><span class="fill" :class="band(cpuOfCap)" :style="{ width: bar(cpuOfCap) }" /></div>
            </div>
            <div class="metric">
              <div class="m-head">
                <span>Memory</span>
                <span class="m-val">
                  {{ fmtBytes(r.mem_used) }}<template v-if="memCap"> / {{ fmtBytes(memCap) }}</template>
                  <span class="muted">{{ memCap ? ` (${pct(r.mem_pct)})` : ' · no limit' }}</span>
                </span>
              </div>
              <div class="meter"><span class="fill" :class="band(r.mem_pct ?? 0)" :style="{ width: bar(r.mem_pct ?? 0) }" /></div>
            </div>
            <dl class="kv">
              <dt>Processes</dt>
              <dd>{{ r.pids ?? '—' }}<span v-if="r.pids_limit ?? c?.pids_limit" class="muted"> / {{ r.pids_limit ?? c?.pids_limit }} max</span></dd>
              <dt>Network</dt>
              <dd>
                <span title="Received since start">↓ {{ fmtBytes(r.net_rx) }}</span>
                <span class="sep" />
                <span title="Sent since start">↑ {{ fmtBytes(r.net_tx) }}</span>
                <span v-if="rates.rx !== null" class="muted"> · {{ fmtRate(rates.rx) }} ↓ {{ fmtRate(rates.tx) }} ↑</span>
              </dd>
              <dt>Disk I/O</dt>
              <dd>
                <template v-if="r.blk_read !== null">read {{ fmtBytes(r.blk_read) }} · written {{ fmtBytes(r.blk_write) }}</template>
                <template v-else>—</template>
              </dd>
            </dl>
            <p class="hint">Totals since the workspace started; rates over the last few seconds.</p>
          </template>
          <p v-else class="hint">{{ ws.status === 'running' ? 'Collecting usage…' : 'Not running.' }}</p>
        </section>

        <!-- Network -->
        <section class="card">
          <h4><component :is="routeIcon" :size="13" /> Network</h4>
          <dl class="kv">
            <template v-if="n.mode === 'gluetun'">
              <dt>VPN profile</dt><dd>{{ n.profile_name ?? '—' }}</dd>
              <dt>Provider</dt><dd>{{ [n.vpn_provider, n.vpn_type].filter(Boolean).join(' · ') || '—' }}</dd>
              <dt>Servers</dt><dd>{{ n.vpn_servers ?? '—' }}</dd>
            </template>
            <template v-else-if="n.mode === 'tailscale'">
              <dt>Profile</dt><dd>{{ n.profile_name ?? '—' }}<span v-if="n.login_server" class="muted"> · {{ host(n.login_server) }}</span></dd>
              <dt>Tailnet IP</dt>
              <dd>
                <template v-if="n.tailscale_ip">
                  <code>{{ n.tailscale_ip }}</code>
                  <button type="button" class="icon-btn" title="Copy" @click="copy(n.tailscale_ip)"><Copy :size="12" /></button>
                </template>
                <span v-else class="muted">not joined yet</span>
              </dd>
              <dt>Hostname</dt><dd><code>{{ n.tailscale_hostname }}</code></dd>
              <dt>Exit node</dt><dd>{{ n.exit_node || 'none' }}</dd>
              <dt>Routes / DNS</dt>
              <dd>{{ n.accept_routes ? 'accepts routes' : 'no routes' }} · {{ n.accept_dns ? 'tailnet DNS' : 'own DNS' }}</dd>
            </template>
            <template v-else>
              <dt>Bridge IP</dt><dd><code>{{ n.bridge_ip ?? '—' }}</code></dd>
              <dt>DNS</dt><dd>{{ n.custom_dns ?? 'host default' }}</dd>
            </template>
            <template v-if="n.sidecar">
              <dt>{{ n.mode === 'gluetun' ? 'Tunnel' : 'Sidecar' }}</dt>
              <dd>
                <span class="dot" :class="sidecarTone" />
                {{ sidecarLabel }}
                <span v-if="n.sidecar.restart_count" class="muted"> · restarted {{ n.sidecar.restart_count }}×</span>
              </dd>
            </template>
            <dt>LAN access</dt>
            <dd>{{ n.lan_access ? n.lan_subnets.join(', ') : 'blocked' }}</dd>
            <dt>Public IP</dt>
            <dd>
              <template v-if="pub">
                <code>{{ pub.ip }}</code>
                <span class="muted"> · {{ [pub.city, pub.country].filter(Boolean).join(', ') }}</span>
                <div v-if="pub.org" class="muted small">{{ pub.org }}</div>
              </template>
              <button v-else type="button" class="link-btn" :disabled="pubLoading || ws.status !== 'running'" @click="checkIp">
                {{ pubLoading ? 'Checking…' : 'Check' }}
              </button>
              <div v-if="pubError" class="small err">{{ pubError }}</div>
            </dd>
          </dl>
          <p class="hint">
            Public IP is looked up from inside the workspace (via ipinfo.io) when you press Check,
            so it shows where its traffic really comes out.
          </p>
          <template v-if="n.mode === 'tailscale' && ws.status === 'running'">
            <button type="button" class="link-btn" @click="toggleTsStatus">
              {{ tsOpen ? 'Hide' : 'Show' }} <code>tailscale status</code>
            </button>
            <pre v-if="tsOpen" class="out">{{ tsStatus ?? 'Loading…' }}</pre>
          </template>
        </section>

        <!-- Storage -->
        <section class="card">
          <h4><HardDrive :size="13" /> Storage</h4>
          <dl class="kv">
            <dt>Home</dt><dd>{{ STORAGE_LABEL[info.storage.kind] }}</dd>
            <template v-if="info.storage.measurable">
              <dt>Size</dt>
              <dd>
                <template v-if="disk">
                  {{ disk.partial ? 'over ' : '' }}{{ fmtBytes(disk.bytes) }}
                  <span class="muted">· {{ disk.files.toLocaleString() }}{{ disk.partial ? '+' : '' }} files</span>
                </template>
                <span v-else class="muted">{{ diskError || 'Measuring…' }}</span>
              </dd>
            </template>
            <dt>Shared memory</dt><dd>{{ c?.shm_size ? fmtBytes(c.shm_size) : '—' }}</dd>
          </dl>
          <p v-if="info.storage.kind === 'ephemeral'" class="hint">Nothing here is kept: the home is wiped when it stops.</p>
          <p v-else-if="disk?.partial" class="hint">Stopped counting after a few seconds; the real total is larger.</p>
        </section>

        <!-- Container -->
        <section class="card">
          <h4><Box :size="13" /> Container</h4>
          <dl class="kv">
            <dt>Image</dt>
            <dd>{{ info.image_name }}<div v-if="info.image_ref" class="small"><code>{{ info.image_ref }}</code></div></dd>
            <template v-if="c">
              <dt>Container</dt><dd><code>{{ c.name }}</code> <span class="muted">{{ c.id }}</span></dd>
              <dt>Image ID</dt><dd><code>{{ c.image_id }}</code></dd>
              <dt>Restarts</dt><dd>{{ c.restart_count }}</dd>
            </template>
            <dt>Zone</dt><dd>{{ info.zone_name ?? 'Local' }}</dd>
            <dt>Options</dt>
            <dd class="chips">
              <span v-for="f in featureChips" :key="f.key" class="chip" :class="{ on: f.on }">{{ f.label }}</span>
            </dd>
            <template v-if="c">
              <dt>Hardening</dt>
              <dd>
                <span class="chip" :class="{ on: c.no_new_privileges }">{{ c.no_new_privileges ? 'no-new-privileges' : 'privileges allowed' }}</span>
                <span v-if="c.privileged" class="chip warn">privileged</span>
              </dd>
            </template>
          </dl>
        </section>
      </div>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { Box, Copy, Gauge, Globe, HardDrive, Lock, Network } from 'lucide-vue-next'
import BaseModal from './BaseModal.vue'
import LoadingSpinner from './LoadingSpinner.vue'
import StatusBadge from './StatusBadge.vue'
import { workspacesApi } from '@/api/workspaces'
import { useUiStore } from '@/stores/ui'
import { displayStatus, fmtBytes, fmtDuration, parseTs } from '@/utils/workspaceStatus'
import type { DiskUsage, PublicIp, SystemInfo, Workspace } from '@/types'

const props = defineProps<{ ws: Workspace; extending?: boolean }>()
const emit = defineEmits<{ extend: [] }>()
const open = defineModel<boolean>({ default: false })
const ui = useUiStore()

const POLL_MS = 4000
const STORAGE_LABEL = {
  persistent: 'Persistent (kept between runs)',
  shared: 'Shared profile (kept, shared with your other shared-profile workspaces)',
  ephemeral: 'Ephemeral',
} as const

const info = ref<SystemInfo | null>(null)
const error = ref('')
const now = ref(Date.now())
const disk = ref<DiskUsage | null>(null)
const diskError = ref('')
const pub = ref<PublicIp | null>(null)
const pubLoading = ref(false)
const pubError = ref('')
const tsOpen = ref(false)
const tsStatus = ref<string | null>(null)
// Byte counters from the previous poll, to turn totals into rates.
const prev = ref<{ t: number; rx: number; tx: number } | null>(null)
const rates = reactive<{ rx: number | null; tx: number | null }>({ rx: null, tx: null })

const r = computed(() => info.value?.resources ?? null)
const c = computed(() => info.value?.container ?? null)
const n = computed(() => info.value!.network)
const memCap = computed(() => r.value?.mem_limit || c.value?.mem_limit || null)
// The meter shows CPU against the container's cap when there is one, so a
// workspace pinned at its limit reads full rather than a modest host share.
const cpuOfCap = computed(() => {
  const pctHost = r.value?.cpu_pct ?? 0
  const cores = r.value?.online_cpus
  const cap = c.value?.cpu_limit
  return cap && cores ? Math.min(100, (pctHost * cores) / cap) : pctHost
})

const uptime = computed(() => {
  const t = parseTs(c.value?.started_at ?? info.value?.started_at ?? null)
  return t === null ? null : fmtDuration(now.value - t)
})
const stopIn = computed(() => {
  const t = parseTs(info.value?.auto_stop_at ?? null)
  return t === null ? null : Math.max(0, t - now.value)
})

const ROUTE: Record<string, { label: string; icon: any }> = {
  direct: { label: 'Direct', icon: Globe },
  gluetun: { label: 'VPN', icon: Lock },
  tailscale: { label: 'Tailscale', icon: Network },
}
const routeIcon = computed(() => ROUTE[info.value?.network.mode ?? 'direct'].icon)
const routeLabel = computed(() => {
  const net = info.value!.network
  return ROUTE[net.mode].label + (net.profile_name ? ` · ${net.profile_name}` : '')
})

const sidecarTone = computed(() => {
  const s = n.value.sidecar
  if (!s || s.state !== 'running') return 'bad'
  if (s.health === 'unhealthy') return 'bad'
  if (s.health === 'starting') return 'warn'
  return 'good'
})
const sidecarLabel = computed(() => {
  const s = n.value.sidecar!
  if (s.state === 'missing') return 'missing'
  if (s.state !== 'running') return s.state ?? 'unknown'
  if (s.health === 'healthy') return n.value.mode === 'gluetun' ? 'connected' : 'running'
  if (s.health === 'starting') return 'connecting…'
  if (s.health === 'unhealthy') return 'down (health check failing)'
  return 'running'
})

const featureChips = computed(() => {
  const f = info.value?.features ?? {}
  return [
    { key: 'wayland', label: f.wayland ? 'Wayland' : 'X11', on: true },
    { key: 'gpu', label: 'GPU', on: !!f.gpu_accel },
    { key: 'docker', label: 'Docker', on: !!f.docker },
    { key: 'sudo', label: 'sudo', on: !!f.sudo },
    { key: 'ssh', label: 'SSH key', on: !!f.ssh_key },
    ...(f.kiosk ? [{ key: 'kiosk', label: 'Kiosk', on: true }] : []),
  ]
})

async function load() {
  try {
    const next = await workspacesApi.info(props.ws.id)
    const res = next.resources
    const t = Date.now()
    if (res?.net_rx != null && res.net_tx != null) {
      if (prev.value && t > prev.value.t) {
        const dt = (t - prev.value.t) / 1000
        rates.rx = Math.max(0, (res.net_rx - prev.value.rx) / dt)
        rates.tx = Math.max(0, (res.net_tx - prev.value.tx) / dt)
      }
      prev.value = { t, rx: res.net_rx, tx: res.net_tx }
    }
    info.value = next
    error.value = ''
  } catch (e: any) {
    error.value = e?.message || 'Failed to load'
  }
}

async function loadDisk() {
  disk.value = null
  diskError.value = ''
  try {
    disk.value = await workspacesApi.diskUsage(props.ws.id)
  } catch (e: any) {
    diskError.value = e?.message || 'Unavailable'
  }
}

async function checkIp() {
  pubLoading.value = true
  pubError.value = ''
  try {
    pub.value = await workspacesApi.publicIp(props.ws.id)
  } catch (e: any) {
    pubError.value = e?.message || 'Check failed'
  } finally {
    pubLoading.value = false
  }
}

async function toggleTsStatus() {
  tsOpen.value = !tsOpen.value
  if (!tsOpen.value) return
  tsStatus.value = null
  try {
    const s = await workspacesApi.tailscaleStatus(props.ws.id)
    tsStatus.value = s.available ? s.output || '(no output)' : 'The Tailscale sidecar is not running.'
  } catch (e: any) {
    tsStatus.value = e?.message || 'Failed to load'
  }
}

let poll: ReturnType<typeof setInterval> | null = null
let tick: ReturnType<typeof setInterval> | null = null
function stopTimers() {
  if (poll) clearInterval(poll)
  if (tick) clearInterval(tick)
  poll = tick = null
}

watch(open, async isOpen => {
  stopTimers()
  if (!isOpen) return
  info.value = null
  error.value = ''
  pub.value = null
  pubError.value = ''
  tsOpen.value = false
  prev.value = null
  rates.rx = rates.tx = null
  await load()
  // (Re-read through a cast: TS still narrows info to null from the reset above.)
  if ((info.value as SystemInfo | null)?.storage.measurable) loadDisk()
  // Pause while the tab is hidden: nobody is reading it, and each poll asks
  // Docker for a stats sample.
  poll = setInterval(() => { if (!document.hidden) load() }, POLL_MS)
  tick = setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })
// Extend changes the auto-stop time; pick it up straight away.
watch(() => props.ws.runtime_extended_at, () => { if (open.value) load() })
onUnmounted(stopTimers)

function pct(v: number | null | undefined) {
  return v == null ? '—' : `${v.toFixed(0)}%`
}
function bar(v: number) {
  return `${Math.max(0, Math.min(100, v))}%`
}
function band(v: number) {
  return v >= 90 ? 'red' : v >= 70 ? 'amber' : 'green'
}
function fmtRate(v: number | null) {
  return v == null ? '—' : `${fmtBytes(v)}/s`
}
function trimNum(v: number) {
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}
function host(url: string) {
  try { return new URL(url).host } catch { return url }
}
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ui.toast('Copied', 'success')
  } catch {
    ui.toast('Copy failed — select it instead', 'error')
  }
}
</script>

<style scoped>
.sys { display: flex; flex-direction: column; gap: 16px; }
.glance {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px;
  padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2);
}
.g-item { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.g-label { font-size: 10px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted); }
.g-val { font-family: var(--font-mono); font-size: 13px; color: var(--text); display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.route.gluetun { color: var(--green); }
.route.tailscale { color: var(--accent-2); }
.stale { font-size: 12px; color: var(--amber); margin: -6px 0 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
.card { border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.card h4 {
  display: flex; align-items: center; gap: 7px; margin: 0;
  font-family: var(--font-display); font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--text);
}
.card h4 svg { color: var(--accent); }
.metric { display: flex; flex-direction: column; gap: 6px; }
.m-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted); }
.m-val { font-family: var(--font-mono); font-size: 13px; color: var(--text); text-transform: none; letter-spacing: 0; text-align: right; }
.meter { height: 6px; border-radius: 3px; background: rgba(255, 255, 255, 0.06); overflow: hidden; }
.fill { display: block; height: 100%; border-radius: 3px; transition: width 0.4s ease; }
.fill.green { background: var(--green); }
.fill.amber { background: var(--amber); }
.fill.red { background: var(--red); }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 7px 14px; margin: 0; font-size: 13px; }
.kv dt { font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase; color: var(--text-muted); padding-top: 1px; white-space: nowrap; }
.kv dd { margin: 0; color: var(--text); font-family: var(--font-mono); font-size: 12px; min-width: 0; overflow-wrap: anywhere; }
.kv code { color: var(--accent); }
.muted { color: var(--text-muted); }
.small { font-size: 11px; margin-top: 2px; }
.err { color: var(--red); }
.sep { display: inline-block; width: 10px; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }
.dot.good { background: var(--green); box-shadow: 0 0 6px var(--green); }
.dot.warn { background: var(--amber); }
.dot.bad { background: var(--red); }
.chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip {
  font-size: 11px; padding: 1px 7px; border: 1px solid var(--border); border-radius: 999px;
  color: var(--text-muted); text-decoration: line-through; text-decoration-color: rgba(255, 255, 255, 0.25);
}
.chip.on { color: var(--text); text-decoration: none; border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); }
.chip.warn { color: var(--amber); text-decoration: none; border-color: var(--amber); }
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 0; font-family: var(--font-mono); }
.link-btn {
  background: none; border: none; padding: 0; color: var(--accent); cursor: pointer;
  font-family: var(--font-mono); font-size: 12px; text-decoration: underline; text-underline-offset: 2px; align-self: flex-start;
}
.link-btn:disabled { color: var(--text-muted); cursor: default; }
.icon-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; padding: 0 4px; vertical-align: middle; }
.icon-btn:hover { color: var(--accent); }
.out {
  margin: 0; max-height: 180px; overflow: auto; padding: 10px; border-radius: var(--radius-sm);
  background: var(--bg); border: 1px solid var(--border); font-size: 11px; line-height: 1.45; white-space: pre;
}
.form-error { color: var(--red); margin: 0; }
</style>
