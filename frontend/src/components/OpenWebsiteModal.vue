<template>
  <BaseModal v-model="open" title="Open Website" width="640px">
    <form @submit.prevent="handleSubmit" class="form">
      <div class="form-group">
        <label>Website URL(s)</label>
        <textarea
          v-model="url"
          rows="3"
          placeholder="https://app.example.com&#10;https://another.example.com"
          required
          autofocus
        />
        <p class="hint">One URL per line — each opens in its own tab (up to 6). Multiple tabs open full-screen with a tab bar.</p>
      </div>

      <!-- Browser picker, mirroring the deploy-node image gallery so the same
           logos identify the same images in both flows. -->
      <div class="form-group">
        <div class="field-label">Browser</div>
        <p v-if="!browsers.length" class="hint empty">No browser images — an admin can sync the catalog from Admin → Images.</p>
        <div v-else class="gallery" role="listbox" aria-label="Browsers">
          <button
            v-for="b in browsers"
            :key="b.id"
            type="button"
            class="card"
            role="option"
            :aria-selected="browserId === b.id"
            :class="{ selected: browserId === b.id }"
            :title="b.name"
            @click="browserId = b.id"
          >
            <span class="card-logo">
              <img v-if="b.logo_url" :src="b.logo_url" :alt="b.name" loading="lazy" />
              <span v-else class="logo-fallback">{{ b.name.charAt(0).toUpperCase() }}</span>
            </span>
            <span class="card-name">{{ b.name }}</span>
          </button>
        </div>
      </div>

      <div v-if="zonesStore.hasRemote" class="form-group">
        <label>Zone</label>
        <select v-model.number="zoneId">
          <option v-for="z in zonesStore.items" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <p class="hint">Which node runs this browser. Local is this Cove host.</p>
      </div>

      <!-- One segmented choice rather than separate toggles: the three routing
           modes are mutually exclusive, so picking one is unambiguous. -->
      <div class="form-group">
        <div class="field-label">Network mode</div>
        <div class="segmented" role="tablist" aria-label="Network mode">
          <button
            type="button" role="tab" :aria-selected="mode === 'direct'"
            :class="{ on: mode === 'direct' }" @click="mode = 'direct'"
          >
            <Globe class="seg-icon" :size="14" aria-hidden="true" />
            <span>Direct</span>
          </button>
          <button
            type="button" role="tab" :aria-selected="mode === 'tailscale'"
            :class="{ on: mode === 'tailscale' }"
            :disabled="!tailscaleReady"
            :title="!tailscaleReady ? 'Add a Tailscale pre-auth key in Preferences to enable' : ''"
            @click="mode = 'tailscale'"
          >
            <svg class="seg-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="TAILSCALE_ICON" /></svg>
            <span>Tailscale</span>
          </button>
          <button
            type="button" role="tab" :aria-selected="mode === 'gluetun'"
            :class="{ on: mode === 'gluetun' }"
            :disabled="!gluetunReady"
            :title="!gluetunReady ? 'Add a Gluetun VPN config in Preferences to enable' : ''"
            @click="mode = 'gluetun'"
          >
            <svg class="seg-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="WIREGUARD_ICON" /></svg>
            <span>Gluetun</span>
          </button>
        </div>

        <template v-if="mode === 'tailscale'">
          <div class="form-group ts-field">
            <label>Exit node (optional)</label>
            <input v-model="tsExitNode" type="text" placeholder="us-nyc-1 or 100.x.y.z" />
          </div>
          <div class="toggles ts-field">
            <ToggleRow v-model="tsAcceptRoutes">Accept routes</ToggleRow>
            <ToggleRow v-model="tsAcceptDns">Accept DNS</ToggleRow>
          </div>
          <p class="hint">Egress routes through your tailnet (auth key in Preferences → Tailscale).</p>
        </template>
        <p v-else-if="mode === 'gluetun'" class="hint">
          Uses your Gluetun VPN config (Preferences → Gluetun). All egress goes through the VPN tunnel.
        </p>
        <p v-else class="hint">Standard bridge networking with the host's default resolver.</p>
      </div>

      <div class="toggles">
        <ToggleRow v-model="ephemeral" :disabled="zoneId !== 0">
          Ephemeral
          <template #hint>
            <template v-if="zoneId !== 0">
              Websites opened on a remote zone are always ephemeral — no browser data is written to the agent's storage.
            </template>
            <template v-else>
              Cookies, history, and downloads live only in the container and are discarded on halt.
            </template>
          </template>
        </ToggleRow>
        <ToggleRow v-if="effectiveEphemeral" v-model="autoRemove">
          Discard when stopped
          <template #hint>
            The node disappears from the grid when it halts, instead of leaving a card that can only start blank.
          </template>
        </ToggleRow>
      </div>

      <div v-if="error" class="form-error">⚠ {{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading" :disabled="!browsers.length">Launch</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { Globe } from 'lucide-vue-next'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import ToggleRow from './ToggleRow.vue'
import { TAILSCALE_ICON, WIREGUARD_ICON } from '@/utils/brandIcons'
import { imagesApi } from '@/api/images'
import { usersApi } from '@/api/users'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useZonesStore } from '@/stores/zones'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type { WorkspaceImage } from '@/types'

const open = defineModel<boolean>({ default: false })

type NetMode = 'direct' | 'tailscale' | 'gluetun'

const images = ref<WorkspaceImage[]>([])
const url = ref('')
const browserId = ref<number | ''>('')
const mode = ref<NetMode>('direct')
const tsExitNode = ref('')
const tsAcceptRoutes = ref(true)
const tsAcceptDns = ref(true)
const ephemeral = ref(false)
const autoRemove = ref(false)
const zoneId = ref(0)
const gluetunReady = ref(false)
const tailscaleReady = ref(false)
const loading = ref(false)
const error = ref('')

const store = useWorkspacesStore()
const zonesStore = useZonesStore()
const ui = useUiStore()
const router = useRouter()

const browsers = computed(() => images.value.filter(i => i.image_type === 'browser' || i.url_env))
// Websites on a remote zone are always ephemeral (no agent-side storage).
const effectiveEphemeral = computed(() => (zoneId.value !== 0 ? true : ephemeral.value))

// auto_remove only exists for ephemeral nodes — the API rejects the pair, so
// never leave it set behind a control the user can no longer see.
watch(effectiveEphemeral, (isEphemeral) => {
  if (!isEphemeral) autoRemove.value = false
})

onMounted(async () => {
  zonesStore.fetch()
  images.value = await imagesApi.list()
  if (browsers.value.length) browserId.value = browsers.value[0].id
  try {
    const g = await usersApi.getGluetun()
    gluetunReady.value = g.enabled && g.has_config
  } catch {
    // Non-fatal: the Gluetun segment just stays disabled.
  }
  try {
    const ts = await usersApi.getTailscale()
    tailscaleReady.value = ts.has_auth_key
  } catch {
    // Non-fatal: the Tailscale segment just stays disabled.
  }
})

function deriveName(u: string): string {
  const first = u.trim().split(/\s+/)[0] || ''
  try {
    return new URL(first).hostname.replace(/^www\./, '')
  } catch {
    return 'website'
  }
}

function reset() {
  url.value = ''
  mode.value = 'direct'
  tsExitNode.value = ''
  tsAcceptRoutes.value = true
  tsAcceptDns.value = true
  ephemeral.value = false
  autoRemove.value = false
  zoneId.value = 0
}

async function handleSubmit() {
  error.value = ''
  loading.value = true
  const name = deriveName(url.value)
  try {
    const ws = await store.launch({
      name,
      image_id: browserId.value as number,
      workspace_type: 'browser',
      zone_id: zoneId.value,
      target_url: url.value,
      ephemeral: effectiveEphemeral.value,
      auto_remove: effectiveEphemeral.value && autoRemove.value,
      use_gluetun: mode.value === 'gluetun',
      use_tailscale: mode.value === 'tailscale',
      ...(mode.value === 'tailscale'
        ? {
            ts_exit_node: tsExitNode.value || undefined,
            ts_accept_routes: tsAcceptRoutes.value,
            ts_accept_dns: tsAcceptDns.value,
          }
        : {}),
    })
    open.value = false
    ui.toast(`Opening ${name}…`, 'info')
    reset()
    router.push(`/app/workspace/${ws.id}`)
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
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 0; }
.hint.empty { padding: 10px 0; }
.ts-field { padding-left: 24px; border-left: 1px solid var(--border); }
.toggles { display: flex; flex-direction: column; gap: 8px; }

.field-label {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 8px;
}

/* ── Browser gallery (matches the deploy-node image chooser) ─────────────── */
.gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));
  gap: 8px;
  max-height: 200px;
  overflow-y: auto;
}
.card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px 8px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.card:hover { border-color: var(--accent); }
.card.selected { border-color: var(--accent); background: var(--accent-dim); box-shadow: var(--glow-sm); }
.card-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
}
.card-logo img { max-width: 100%; max-height: 100%; object-fit: contain; }
.logo-fallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  border-radius: var(--radius-sm);
  background: var(--accent-dim);
  color: var(--accent);
  font-family: var(--font-display);
  font-size: 15px;
}
.card-name {
  font-size: 11.5px;
  line-height: 1.3;
  text-align: center;
  overflow-wrap: anywhere;
}

/* ── Network mode (matches NetworkFields) ────────────────────────────────── */
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

.segmented + .form-group,
.segmented + .toggles,
.segmented + .hint { margin-top: 14px; }
</style>
