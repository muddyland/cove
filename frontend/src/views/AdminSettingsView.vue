<template>
  <AppShell>
    <div class="page-header">
      <h2>// SETTINGS</h2>
    </div>

    <div class="panels">
      <section class="panel">
        <h3>SYSTEM</h3>
        <nav class="section-nav" aria-label="Settings sections">
          <a v-for="sct in SECTIONS" :key="sct.id" :href="`#set-${sct.id}`" @click.prevent="jump(sct.id)">{{ sct.label }}</a>
        </nav>
        <LoadingSpinner v-if="loading" block />
        <form v-else class="form" @submit.prevent="handleSave">
          <h4 id="set-images" class="section-h">Sidecar images</h4>
          <div class="form-group">
            <label>Tailscale image</label>
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
            <label>Gluetun image</label>
            <input
              v-model="form.gluetun_image"
              type="text"
              placeholder="qmcgaw/gluetun:latest"
              autocomplete="off"
            />
            <p class="hint">
              Container image for the per-workspace Gluetun VPN sidecar. Pin a tag/digest
              for reproducible deployments.
            </p>
          </div>

          <h4 id="set-lifetime" class="section-h">Lifetime &amp; retention</h4>
          <div class="form-group">
            <label>Max workspace runtime (hours)</label>
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
            <label>Trash retention (days)</label>
            <input
              v-model.number="form.trash_retention_days"
              type="number"
              min="0"
              placeholder="30"
            />
            <p class="hint">
              Keep soft-deleted files in the file-browser trash this many days, then
              auto-purge them. 0 = keep until a user empties the trash.
            </p>
          </div>

          <h4 id="set-limits" class="section-h">Resource limits</h4>
          <div class="form-group">
            <label>Default CPU limit (cores)</label>
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
            <label>Default memory limit (MB)</label>
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

          <div class="form-group">
            <label>Process limit (PIDs)</label>
            <input
              v-model.number="form.workspace_pids_limit"
              type="number"
              min="0"
              step="1024"
              placeholder="8192"
            />
            <p class="hint">
              Max processes + threads per workspace container (a fork bomb otherwise
              exhausts the host). 0 = unlimited. Default <code>8192</code> is generous for a
              desktop with a busy browser.
            </p>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>Workspaces per account</label>
              <input v-model.number="form.default_max_workspaces" type="number" min="0" max="1000" placeholder="0" />
            </div>
            <div class="form-group">
              <label>Running at once per account</label>
              <input v-model.number="form.default_max_running" type="number" min="0" max="1000" placeholder="0" />
            </div>
          </div>
          <p class="hint">
            How many workspaces each account may have, and how many of them may run at the same
            time. 0 = no limit. Override them for one account under Admin → Users; admins are never
            limited. Existing workspaces past a new limit stay, but no new one can be added (or
            started) until the account is back under it.
          </p>

          <h4 id="set-network" class="section-h">Network</h4>
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
            <label>Allowed LAN subnets</label>
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

          <h4 id="set-security" class="section-h">Security</h4>
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

          <h4 id="set-signin" class="section-h">Sign-in</h4>
          <label class="checkbox-row">
            <input type="checkbox" v-model="form.require_2fa_all" />
            <span>Require two-factor sign-in for every local account</span>
          </label>
          <p class="hint">
            Accounts without an authenticator app or a passkey set one up at their next sign-in. To
            require it of only some accounts, tick it per account under Admin → Users. SSO accounts
            use their identity provider's two-factor sign-in instead.
          </p>
          <div class="form-group">
            <label>LAN networks</label>
            <input v-model="form.signin_lan_networks" type="text" autocomplete="off"
                   placeholder="10.0.0.0/8, 192.168.0.0/16, fc00::/7, fe80::/10" />
            <p class="hint">
              Where accounts marked <strong>Local network only</strong> (Admin → Users) may sign in
              from. Comma separated IPv4/IPv6 CIDRs; invalid entries are dropped on save.
              <code>172.16.0.0/12</code> isn't in the default because Docker can show visitors from
              the internet as its bridge gateway (<code>172.17.0.1</code>); add your own range if
              your LAN uses it.
            </p>
          </div>
          <div class="form-group">
            <label>Trusted proxies</label>
            <input v-model="form.trusted_proxies" type="text" autocomplete="off" placeholder="none" />
            <p class="hint">
              Reverse proxies or tunnels in front of Cove's Traefik, so Cove sees each visitor's own
              address instead of the proxy's. List them in Traefik's
              <code>forwardedHeaders.trustedIPs</code> too (see Deployment). Leave empty when
              browsers reach Cove's Traefik directly.
            </p>
          </div>
          <div class="netcheck" v-if="netCheck">
            <span>This connection: <code>{{ netCheck.client_ip }}</code></span>
            <span :class="netCheck.is_lan ? 'ok' : 'warn'">{{ netCheck.is_lan ? 'local network' : 'outside the local network' }}</span>
            <p v-if="netCheck.warning" class="hint warn">{{ netCheck.warning }}</p>
            <p class="hint">
              Saved settings apply here. Check from a phone off the Wi-Fi too: it must show
              <em>outside</em>, or Local network only won't protect anything.
            </p>
          </div>

          <h4 id="set-gpu" class="section-h">GPU</h4>
          <label class="checkbox-row">
            <input type="checkbox" v-model="form.workspace_gpu_accel" />
            <span>Enable GPU acceleration</span>
          </label>
          <p class="hint">
            Master switch for hardware (VAAPI) video encode. When <strong>on</strong>, workspaces
            can opt in to bind-mount the host's DRI render node so the stream is GPU-encoded
            instead of on the CPU. Only enable on hosts that actually have a usable GPU — a wrong
            device/GID makes opted-in workspaces fail to launch.
          </p>

          <div v-if="form.workspace_gpu_accel" class="form-group">
            <label>Render node</label>
            <input type="text" v-model="form.workspace_gpu_render_node" placeholder="/dev/dri/renderD128" />
            <p class="hint">
              Host path to the DRI render node bind-mounted into GPU workspaces (used for both
              <code>DRINODE</code> and <code>DRI_NODE</code>, enabling zero-copy encode).
            </p>
          </div>
          <div v-if="form.workspace_gpu_accel" class="form-group">
            <label>Render group GID</label>
            <input type="number" v-model.number="form.workspace_gpu_render_gid" placeholder="992" />
            <p class="hint">
              Numeric group id that owns the render node on the host (<code>stat -c %g</code> of the
              device — often 992 on Debian, 44/993 elsewhere). Added to the container so its user can
              open the device.
            </p>
          </div>

          <h4 id="set-docker" class="section-h">Docker-in-Docker</h4>
          <label class="checkbox-row">
            <input type="checkbox" v-model="form.workspace_docker" />
            <span>Allow Docker-in-Docker</span>
          </label>
          <p class="hint">
            Master switch for in-workspace Docker (dev container support). When
            <strong>on</strong>, accounts you have granted <em>Allow Docker-in-Docker</em> (and
            admins) can opt a workspace into a <strong>privileged</strong> nested Docker daemon.
            Privileged means exactly that: anyone holding it is effectively <strong>root on this
            host</strong> — the nested daemon can mount host disks and start host-network
            containers. Treat the per-user grant as handing out host root. Off by default.
          </p>

          <div v-if="form.workspace_docker" class="form-group">
            <label>DinD image</label>
            <input type="text" v-model="form.dind_image" placeholder="docker:dind" />
            <p class="hint">
              The Docker-in-Docker image the sidecar runs (multi-arch; works on x86_64 and arm64).
            </p>
          </div>

          <div v-if="error" class="form-error">⚠ {{ error }}</div>
          <!-- Sticky so Save is reachable from anywhere in this long form. -->
          <div class="form-actions sticky-save">
            <span v-if="dirty" class="unsaved">Unsaved changes</span>
            <NeonButton type="submit" variant="primary" :loading="saving" :disabled="!dirty && !saving">Save settings</NeonButton>
          </div>
        </form>
      </section>

      <section class="panel">
        <h3>ENVIRONMENT</h3>
        <p class="hint env-note">
          Read-only summary of configuration derived from environment variables. To change these,
          update the environment and restart the server. Secrets are masked.
        </p>
        <LoadingSpinner v-if="envLoading" block />
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
import { ref, reactive, computed, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import NeonButton from '@/components/NeonButton.vue'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import type { EnvEntry, NetworkCheck } from '@/types'

const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const error = ref('')

// Saved state, to tell the user when the long form has unsaved edits.
const snapshot = ref('')
const dirty = computed(() => !!snapshot.value && JSON.stringify(form) !== snapshot.value)

const SECTIONS = [
  { id: 'images', label: 'Images' },
  { id: 'lifetime', label: 'Lifetime' },
  { id: 'limits', label: 'Limits' },
  { id: 'network', label: 'Network' },
  { id: 'security', label: 'Security' },
  { id: 'signin', label: 'Sign-in' },
  { id: 'gpu', label: 'GPU' },
  { id: 'docker', label: 'Docker' },
]
function jump(id: string) {
  document.getElementById(`set-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const envLoading = ref(true)
const envError = ref('')
const envEntries = ref<EnvEntry[]>([])

const form = reactive({
  tailscale_image: '',
  gluetun_image: '',
  workspace_lan_access: false,
  workspace_lan_subnets: '',
  workspace_no_new_privileges: false,
  workspace_max_runtime_hours: 24,
  workspace_cpu_limit: 0,
  workspace_memory_limit_mb: 0,
  workspace_pids_limit: 8192,
  workspace_gpu_accel: false,
  workspace_gpu_render_node: '/dev/dri/renderD128',
  workspace_gpu_render_gid: 992,
  workspace_docker: false,
  dind_image: 'docker:dind',
  trash_retention_days: 30,
  default_max_workspaces: 0,
  default_max_running: 0,
  signin_lan_networks: '',
  trusted_proxies: '',
  require_2fa_all: false,
})
const netCheck = ref<NetworkCheck | null>(null)
function loadNetCheck() {
  adminApi.networkCheck().then(n => { netCheck.value = n }).catch(() => { netCheck.value = null })
}

onMounted(async () => {
  loadNetCheck()
  try {
    const settings = await adminApi.settings.get()
    form.tailscale_image = settings.tailscale_image
    form.gluetun_image = settings.gluetun_image
    form.workspace_lan_access = settings.workspace_lan_access
    form.workspace_lan_subnets = settings.workspace_lan_subnets
    form.workspace_no_new_privileges = settings.workspace_no_new_privileges
    form.workspace_max_runtime_hours = settings.workspace_max_runtime_hours
    form.workspace_cpu_limit = settings.workspace_cpu_limit
    form.workspace_memory_limit_mb = settings.workspace_memory_limit_mb
    form.workspace_pids_limit = settings.workspace_pids_limit
    form.workspace_gpu_accel = settings.workspace_gpu_accel
    form.workspace_gpu_render_node = settings.workspace_gpu_render_node
    form.workspace_gpu_render_gid = settings.workspace_gpu_render_gid
    form.workspace_docker = settings.workspace_docker
    form.dind_image = settings.dind_image
    form.trash_retention_days = settings.trash_retention_days
    form.default_max_workspaces = settings.default_max_workspaces
    form.default_max_running = settings.default_max_running
    form.signin_lan_networks = settings.signin_lan_networks
    form.trusted_proxies = settings.trusted_proxies
    form.require_2fa_all = settings.require_2fa_all
    snapshot.value = JSON.stringify(form)
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
      gluetun_image: form.gluetun_image,
      workspace_lan_access: form.workspace_lan_access,
      workspace_lan_subnets: form.workspace_lan_subnets,
      workspace_no_new_privileges: form.workspace_no_new_privileges,
      workspace_max_runtime_hours: form.workspace_max_runtime_hours,
      workspace_cpu_limit: form.workspace_cpu_limit,
      workspace_memory_limit_mb: form.workspace_memory_limit_mb,
      workspace_pids_limit: form.workspace_pids_limit,
      workspace_gpu_accel: form.workspace_gpu_accel,
      workspace_gpu_render_node: form.workspace_gpu_render_node,
      workspace_gpu_render_gid: form.workspace_gpu_render_gid,
      workspace_docker: form.workspace_docker,
      dind_image: form.dind_image,
      trash_retention_days: form.trash_retention_days,
      default_max_workspaces: form.default_max_workspaces || 0,
      default_max_running: form.default_max_running || 0,
      signin_lan_networks: form.signin_lan_networks,
      trusted_proxies: form.trusted_proxies,
      require_2fa_all: form.require_2fa_all,
    })
    form.tailscale_image = updated.tailscale_image
    form.gluetun_image = updated.gluetun_image
    form.workspace_lan_access = updated.workspace_lan_access
    form.workspace_lan_subnets = updated.workspace_lan_subnets
    form.workspace_no_new_privileges = updated.workspace_no_new_privileges
    form.workspace_max_runtime_hours = updated.workspace_max_runtime_hours
    form.workspace_cpu_limit = updated.workspace_cpu_limit
    form.workspace_memory_limit_mb = updated.workspace_memory_limit_mb
    form.workspace_pids_limit = updated.workspace_pids_limit
    form.workspace_gpu_accel = updated.workspace_gpu_accel
    form.workspace_gpu_render_node = updated.workspace_gpu_render_node
    form.workspace_gpu_render_gid = updated.workspace_gpu_render_gid
    form.workspace_docker = updated.workspace_docker
    form.dind_image = updated.dind_image
    form.trash_retention_days = updated.trash_retention_days
    form.default_max_workspaces = updated.default_max_workspaces
    form.default_max_running = updated.default_max_running
    form.signin_lan_networks = updated.signin_lan_networks
    form.trusted_proxies = updated.trusted_proxies
    form.require_2fa_all = updated.require_2fa_all
    loadNetCheck()
    snapshot.value = JSON.stringify(form)
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
.netcheck { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; padding: 10px 12px; margin-bottom: 16px; border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: 13px; }
.netcheck .hint { flex-basis: 100%; margin: 0; }
.netcheck .ok { color: var(--green); }
.netcheck .warn { color: var(--amber); }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-row .form-group { margin-bottom: 0; }

/* Two columns that use the width on desktop (the long form gets the bigger
   share), one column on narrow screens. */
.panels {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr);
  gap: 24px;
  align-items: start;
}
@media (max-width: 1100px) { .panels { grid-template-columns: minmax(0, 1fr); } }
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  min-width: 0;
}
.panel h3 {
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--text);
  margin-bottom: 14px;
}
.section-nav { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 18px; }
.section-nav a {
  font-family: var(--font-mono); font-size: 12px; color: var(--text-muted);
  border: 1px solid var(--border); border-radius: 999px; padding: 2px 10px;
}
.section-nav a:hover { color: var(--accent); border-color: var(--accent); text-shadow: none; }
.section-h {
  font-family: var(--font-mono); font-size: 12px; letter-spacing: 2px; text-transform: uppercase;
  color: var(--accent); padding-top: 14px; margin-top: 6px; border-top: 1px solid var(--border);
  scroll-margin-top: 16px;
}
.section-h:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
.sticky-save {
  position: sticky; bottom: 0; z-index: 2;
  margin: 0 -24px -24px; padding: 12px 24px;
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  backdrop-filter: blur(4px);
  border-top: 1px solid var(--border);
  align-items: center; gap: 12px;
}
.unsaved { font-family: var(--font-mono); font-size: 12px; color: var(--amber); }
.form { display: flex; flex-direction: column; gap: 14px; }
.form-actions { display: flex; justify-content: flex-end; }
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 13px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
  margin-bottom: 0;
}
.checkbox-row input { width: auto; margin: 0; }
.hint {
  font-size: 13px;
  line-height: 1.55;
  color: var(--text-muted);
  margin: 4px 0 0;
  max-width: 75ch;
}
.hint code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent);
}

.env-note { margin: 0 0 16px; }
.env-table td { font-family: var(--font-mono); font-size: 12px; vertical-align: top; }
.env-name { color: var(--text-muted); white-space: nowrap; }
.env-value { color: var(--text); word-break: break-all; }
.env-table .empty { padding: 24px; }
</style>
