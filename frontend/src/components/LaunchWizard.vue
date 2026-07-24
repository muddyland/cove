<template>
  <BaseModal v-model="open" title="New Workspace" width="640px">
    <!-- Step header / progress -->
    <ol class="steps" aria-label="Progress">
      <li
        v-for="(s, i) in visibleSteps"
        :key="s.key"
        :class="{ active: s.key === step, done: stepIndex(s.key) < stepIndex(step) }"
      >
        <span class="dot">{{ i + 1 }}</span>
        <span class="step-label">{{ s.label }}</span>
      </li>
    </ol>

    <!-- Step 1: Choose an image -->
    <section v-if="step === 'choose'" class="wizard-step">
      <div class="chooser-controls">
        <input v-model="search" class="search" type="search" placeholder="Search images…" aria-label="Search images" />
        <div class="chips">
          <button
            v-for="f in typeFilters"
            :key="f.value"
            type="button"
            class="chip"
            :class="{ on: typeFilter === f.value }"
            @click="typeFilter = f.value"
          >{{ f.label }}</button>
        </div>
      </div>
      <p v-if="!filteredImages.length" class="hint empty">No images match. An admin can add images from Admin → Images.</p>
      <div v-else class="gallery" role="listbox" aria-label="Images">
        <button
          v-for="img in filteredImages"
          :key="img.id"
          type="button"
          class="card"
          role="option"
          :aria-selected="form.image_id === img.id"
          :class="{ selected: form.image_id === img.id }"
          :title="img.name"
          @click="selectImage(img)"
          @dblclick="selectImage(img); goNext()"
        >
          <span class="card-logo">
            <img v-if="img.logo_url" :src="img.logo_url" :alt="img.name" loading="lazy" />
            <span v-else class="logo-fallback">{{ img.name.charAt(0).toUpperCase() }}</span>
          </span>
          <span class="card-body">
            <span class="card-name">{{ img.name }}</span>
            <span class="card-type" :class="img.image_type">{{ img.image_type }}</span>
            <span v-if="img.description" class="card-desc">{{ img.description }}</span>
          </span>
        </button>
      </div>
    </section>

    <!-- Step 2: Basics -->
    <section v-else-if="step === 'basics'" class="wizard-step form">
      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="My Desktop" required @input="nameEdited = true" />
        <p v-if="showError('name')" class="field-error">Give your workspace a name.</p>
      </div>

      <div v-if="zonesStore.hasRemote" class="form-group">
        <label>Zone</label>
        <select v-model.number="form.zone_id">
          <option v-for="z in zonesStore.items" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <p class="hint">Which node runs this workspace. Local is this Cove host.</p>
      </div>

      <div v-if="urlCapable" class="form-group">
        <label>{{ urlRequired ? 'Target URL(s)' : 'Open URL(s) (optional)' }}</label>
        <textarea v-model="form.target_url" rows="2" placeholder="https://example.com" :required="urlRequired" />
        <p class="hint">One URL per line — each opens in its own tab (up to 6).</p>
        <p v-if="showError('url')" class="field-error">A target URL is required for this image.</p>
      </div>
      <label v-if="urlCapable && urlCount <= 1" class="checkbox-row">
        <input type="checkbox" v-model="form.kiosk" />
        <span>Kiosk mode (full-screen, no browser chrome)</span>
      </label>
      <p v-if="urlCapable && urlCount > 1" class="hint">
        Multiple tabs open full-screen with a tab bar — kiosk lock is unavailable (it would hide the tabs).
      </p>
      <template v-if="urlCapable && urlCount <= 1 && form.kiosk">
        <label class="checkbox-row ts-field"><input type="checkbox" v-model="form.kiosk_dark" /><span>Dark mode</span></label>
        <label class="checkbox-row ts-field"><input type="checkbox" v-model="form.kiosk_menu" /><span>Allow right-click / refresh menu</span></label>
      </template>
      <template v-if="urlCapable">
        <label class="checkbox-row"><input type="checkbox" v-model="form.ephemeral" /><span>Ephemeral (no saved data — wiped when halted)</span></label>
        <p v-if="form.ephemeral" class="hint ts-field">
          Cookies, history, and downloads live only in the container and are discarded on halt.
        </p>
      </template>

      <p v-if="!urlCapable" class="hint ready-note">Ready to launch — or customize networking &amp; apps below.</p>
    </section>

    <!-- Step 3: Advanced (network + apps) — reuses the existing field groups for now -->
    <section v-else-if="step === 'advanced'" class="wizard-step form">
      <WorkspaceOptionsFields
        :form="form"
        :lan-policy="lanPolicy"
        :gluetun-ready="gluetunReady"
        :gpu-enabled="gpuPolicy.enabled"
        :docker-enabled="dockerPolicy.enabled && form.zone_id === 0"
      />
    </section>

    <!-- Step 4: Review -->
    <section v-else-if="step === 'review'" class="wizard-step">
      <dl class="review">
        <div><dt>Image</dt><dd>{{ selectedImage?.name }} <span class="muted">({{ selectedImage?.image_type }})</span></dd></div>
        <div><dt>Name</dt><dd>{{ form.name || '—' }}</dd></div>
        <div v-if="zonesStore.hasRemote"><dt>Zone</dt><dd>{{ zoneName }}</dd></div>
        <div v-if="urlCapable && form.target_url.trim()"><dt>URL(s)</dt><dd>{{ form.target_url.trim() }}</dd></div>
        <div><dt>Network</dt><dd>{{ networkSummary }}</dd></div>
        <div><dt>Access</dt><dd>{{ accessSummary }}</dd></div>
        <div v-if="appsSummary"><dt>Apps</dt><dd>{{ appsSummary }}</dd></div>
      </dl>
      <p class="hint">SSH key injection and Wayland streaming are on by default. You can change these under Customize.</p>
    </section>

    <div v-if="error" class="form-error">{{ error }}</div>

    <!-- Footer navigation -->
    <div class="wizard-footer">
      <NeonButton v-if="step !== 'choose'" type="button" variant="secondary" @click="goBack">Back</NeonButton>
      <span class="spacer" />
      <template v-if="step === 'choose'">
        <NeonButton type="button" variant="primary" :disabled="!form.image_id" @click="goNext">Next</NeonButton>
      </template>
      <template v-else-if="step === 'basics'">
        <NeonButton type="button" variant="secondary" @click="goToAdvanced">Customize →</NeonButton>
        <NeonButton type="button" variant="primary" :loading="loading" @click="launch">Launch</NeonButton>
      </template>
      <template v-else-if="step === 'advanced'">
        <NeonButton type="button" variant="secondary" @click="step = 'review'">Review →</NeonButton>
        <NeonButton type="button" variant="primary" :loading="loading" @click="launch">Launch</NeonButton>
      </template>
      <template v-else>
        <NeonButton type="button" variant="primary" :loading="loading" @click="launch">Launch</NeonButton>
      </template>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import WorkspaceOptionsFields from './WorkspaceOptionsFields.vue'
import { imagesApi } from '@/api/images'
import { workspacesApi } from '@/api/workspaces'
import { usersApi } from '@/api/users'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useZonesStore } from '@/stores/zones'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type { DockerPolicy, GpuPolicy, ImageType, LanPolicy, WorkspaceImage } from '@/types'

const open = defineModel<boolean>({ default: false })

type StepKey = 'choose' | 'basics' | 'advanced' | 'review'
const STEPS: { key: StepKey; label: string }[] = [
  { key: 'choose', label: 'Choose' },
  { key: 'basics', label: 'Set up' },
  { key: 'advanced', label: 'Customize' },
  { key: 'review', label: 'Review' },
]
const step = ref<StepKey>('choose')
// The linear "early launch" path skips Customize; it only appears once the user
// opts into it, so the progress rail shows either 3 or 4 steps.
const visibleSteps = computed(() =>
  STEPS.filter(s => s.key !== 'advanced' || customizing.value),
)
const customizing = ref(false)
function stepIndex(k: StepKey) {
  return visibleSteps.value.findIndex(s => s.key === k)
}

const images = ref<WorkspaceImage[]>([])
const lanPolicy = ref<LanPolicy>({ enabled: false, subnets: [] })
const gpuPolicy = ref<GpuPolicy>({ enabled: false })
const dockerPolicy = ref<DockerPolicy>({ enabled: false })
const gluetunReady = ref(false)
const loading = ref(false)
const error = ref('')
const submitted = ref(false)
const nameEdited = ref(false)
const search = ref('')
const typeFilter = ref<ImageType | 'all'>('all')
const typeFilters: { label: string; value: ImageType | 'all' }[] = [
  { label: 'All', value: 'all' },
  { label: 'Desktop', value: 'desktop' },
  { label: 'App', value: 'app' },
  { label: 'Browser', value: 'browser' },
  { label: 'Link', value: 'link' },
]

const store = useWorkspacesStore()
const zonesStore = useZonesStore()
const ui = useUiStore()
const router = useRouter()

const form = reactive({
  name: '',
  image_id: '' as number | '',
  zone_id: 0 as number,
  target_url: '',
  kiosk: false,
  kiosk_dark: false,
  kiosk_menu: false,
  use_tailscale: false,
  use_gluetun: false,
  ephemeral: false,
  lan_access: false,
  ts_exit_node: '',
  ts_accept_routes: true,
  ts_accept_dns: true,
  custom_dns: false,
  dns_servers: '',
  allow_sudo: false,
  inject_ssh_key: true,
  pixelflux_wayland: true,
  clear_browser_lock: false,
  gpu_accel: false,
  use_docker: false,
  install_packages: '',
  proot_apps: [] as string[],
  appimages: '',
})

const selectedImage = computed(() => images.value.find(i => i.id === form.image_id))
const urlCapable = computed(() =>
  !!selectedImage.value && (!!selectedImage.value.url_env || selectedImage.value.image_type === 'link'),
)
const urlRequired = computed(() => selectedImage.value?.image_type === 'link')
const urlCount = computed(() => form.target_url.trim().split(/\s+/).filter(Boolean).length)

const filteredImages = computed(() => {
  const q = search.value.trim().toLowerCase()
  return images.value.filter(img => {
    if (typeFilter.value !== 'all' && img.image_type !== typeFilter.value) return false
    if (!q) return true
    return (
      img.name.toLowerCase().includes(q) ||
      (img.description ?? '').toLowerCase().includes(q)
    )
  })
})

const zoneName = computed(() => zonesStore.items.find(z => z.id === form.zone_id)?.name ?? 'Local')
const networkSummary = computed(() => {
  if (form.use_tailscale) return 'Tailscale' + (form.ts_exit_node ? ` (exit: ${form.ts_exit_node})` : '')
  if (form.use_gluetun) return 'Gluetun VPN'
  if (form.custom_dns && form.dns_servers.trim()) return `Custom DNS (${form.dns_servers.trim()})`
  return 'Direct' + (form.lan_access ? ' + LAN' : '')
})
const accessSummary = computed(() => {
  const parts: string[] = []
  parts.push(form.inject_ssh_key ? 'SSH key' : 'no SSH key')
  if (form.allow_sudo) parts.push('sudo')
  if (form.gpu_accel) parts.push('GPU')
  if (form.use_docker) parts.push('Docker')
  return parts.join(', ')
})
const appsSummary = computed(() => {
  const parts: string[] = []
  if (form.proot_apps.length) parts.push(`${form.proot_apps.length} proot-app(s)`)
  if (form.install_packages.trim()) parts.push('packages')
  if (form.appimages.trim()) parts.push('AppImages')
  return parts.join(', ')
})

function showError(field: 'name' | 'url'): boolean {
  if (!submitted.value) return false
  if (field === 'name') return !form.name.trim()
  if (field === 'url') return urlRequired.value && !form.target_url.trim()
  return false
}

function selectImage(img: WorkspaceImage) {
  form.image_id = img.id
  // Prefill the name from the image the first time, until the user edits it.
  if (!nameEdited.value) form.name = img.name
}

function goNext() {
  if (step.value === 'choose' && form.image_id) step.value = 'basics'
}
function goToAdvanced() {
  customizing.value = true
  step.value = 'advanced'
}
function goBack() {
  if (step.value === 'basics') step.value = 'choose'
  else if (step.value === 'advanced') step.value = 'basics'
  else if (step.value === 'review') step.value = customizing.value ? 'advanced' : 'basics'
}

onMounted(async () => {
  zonesStore.fetch()
  images.value = await imagesApi.list()
  try {
    lanPolicy.value = await workspacesApi.lanPolicy()
  } catch {
    // Non-fatal: the LAN checkbox just stays hidden if the policy can't load.
  }
  try {
    gpuPolicy.value = await workspacesApi.gpuPolicy()
  } catch {
    // Non-fatal: the GPU checkbox just stays hidden if the policy can't load.
  }
  try {
    dockerPolicy.value = await workspacesApi.dockerPolicy()
  } catch {
    // Non-fatal: the Docker checkbox just stays hidden if the policy can't load.
  }
  try {
    const g = await usersApi.getGluetun()
    gluetunReady.value = g.enabled && g.has_config
  } catch {
    // Non-fatal: Gluetun toggle just stays hidden.
  }
})

function resetForm() {
  step.value = 'choose'
  customizing.value = false
  submitted.value = false
  nameEdited.value = false
  search.value = ''
  typeFilter.value = 'all'
  Object.assign(form, {
    name: '', image_id: '', zone_id: 0, target_url: '',
    kiosk: false, kiosk_dark: false, kiosk_menu: false,
    use_tailscale: false, use_gluetun: false, ephemeral: false, lan_access: false,
    ts_exit_node: '', ts_accept_routes: true, ts_accept_dns: true,
    custom_dns: false, dns_servers: '', allow_sudo: false, inject_ssh_key: true,
    pixelflux_wayland: true, clear_browser_lock: false, gpu_accel: false,
    use_docker: false, install_packages: '', proot_apps: [], appimages: '',
  })
}

async function launch() {
  submitted.value = true
  // Guard the required fields (name + image, and URL for link images) so we don't
  // bounce off a server 422; jump the user to the offending step.
  if (!form.image_id) { step.value = 'choose'; return }
  if (!form.name.trim() || (urlRequired.value && !form.target_url.trim())) { step.value = 'basics'; return }
  error.value = ''
  loading.value = true
  try {
    const ws = await store.launch({
      name: form.name,
      image_id: form.image_id as number,
      workspace_type: selectedImage.value?.image_type ?? 'desktop',
      zone_id: form.zone_id,
      target_url: urlCapable.value && form.target_url ? form.target_url : undefined,
      kiosk: urlCapable.value && urlCount.value <= 1 ? form.kiosk : false,
      kiosk_dark: urlCapable.value && urlCount.value <= 1 && form.kiosk ? form.kiosk_dark : false,
      kiosk_menu: urlCapable.value && urlCount.value <= 1 && form.kiosk ? form.kiosk_menu : false,
      use_tailscale: form.use_tailscale,
      use_gluetun: form.use_gluetun,
      ephemeral: urlCapable.value ? form.ephemeral : false,
      lan_access: form.lan_access,
      ...(form.use_tailscale
        ? {
            ts_exit_node: form.ts_exit_node || undefined,
            ts_accept_routes: form.ts_accept_routes,
            ts_accept_dns: form.ts_accept_dns,
          }
        : {
            custom_dns: form.custom_dns,
            ...(form.custom_dns && form.dns_servers.trim() ? { dns_servers: form.dns_servers.trim() } : {}),
          }),
      allow_sudo: form.allow_sudo,
      inject_ssh_key: form.inject_ssh_key,
      pixelflux_wayland: form.pixelflux_wayland,
      clear_browser_lock: form.clear_browser_lock,
      gpu_accel: form.gpu_accel,
      use_docker: form.use_docker,
      ...(form.install_packages.trim() ? { install_packages: form.install_packages.trim() } : {}),
      ...(form.proot_apps.length ? { proot_apps: form.proot_apps.join(' ') } : {}),
      ...(form.appimages.trim() ? { appimages: form.appimages.trim() } : {}),
    })
    const name = form.name
    open.value = false
    ui.toast(`Launching ${name}…`, 'info')
    resetForm()
    router.push(`/app/workspace/${ws.id}`)
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.wizard-step { min-height: 220px; }
.form { display: flex; flex-direction: column; gap: 16px; }

/* Progress rail */
.steps {
  list-style: none; display: flex; gap: 6px; margin: 0 0 18px; padding: 0;
  align-items: center;
}
.steps li {
  display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0;
  color: var(--text-muted); font-size: 11px; letter-spacing: 0.5px;
}
.steps li:not(:last-child)::after {
  content: ''; flex: 1; height: 1px; background: var(--border); margin-left: 6px;
}
.steps .dot {
  width: 20px; height: 20px; border-radius: 50%; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--border); font-size: 11px;
}
.steps li.active { color: var(--accent); }
.steps li.active .dot { border-color: var(--accent); color: var(--accent); box-shadow: var(--glow-sm); }
.steps li.done .dot { border-color: var(--accent); color: var(--accent); }
.step-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Chooser */
.chooser-controls { display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; }
.search { width: 100%; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  background: none; border: 1px solid var(--border); color: var(--text-muted);
  border-radius: 999px; padding: 3px 12px; font-size: 11px; cursor: pointer;
  text-transform: capitalize; transition: all 0.15s;
}
.chip.on { border-color: var(--accent); color: var(--accent); }
.gallery {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 10px; max-height: 340px; overflow-y: auto; padding: 2px;
}
.card {
  display: flex; gap: 10px; align-items: flex-start; text-align: left;
  background: var(--surface-2, rgba(255,255,255,0.02));
  border: 1px solid var(--border); border-radius: var(--radius);
  padding: 10px; cursor: pointer; transition: all 0.15s;
}
.card:hover { border-color: var(--accent); }
.card.selected { border-color: var(--accent); box-shadow: var(--glow-sm); }
.card-logo {
  width: 36px; height: 36px; flex: none; border-radius: 8px; overflow: hidden;
  display: inline-flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,0.04);
}
.card-logo img { width: 100%; height: 100%; object-fit: contain; }
.logo-fallback { font-family: var(--font-display); color: var(--accent); font-size: 16px; }
.card-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.card-name {
  font-size: 13px; color: var(--text); line-height: 1.3;
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; overflow-wrap: anywhere;
}
.card-type {
  font-size: 9px; letter-spacing: 1px; text-transform: uppercase;
  color: var(--text-muted); width: fit-content;
}
.card-type.desktop { color: var(--accent); }
.card-type.app { color: #7fe081; }
.card-type.browser { color: var(--pink, #ff00aa); }
.card-desc {
  font-size: 10px; color: var(--text-muted); line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.empty { padding: 24px 0; text-align: center; }

/* Review */
.review { display: flex; flex-direction: column; gap: 10px; margin: 0 0 14px; }
.review > div { display: grid; grid-template-columns: 90px 1fr; gap: 10px; align-items: baseline; }
.review dt { font-size: 10px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted); margin: 0; }
.review dd { margin: 0; font-size: 13px; color: var(--text); word-break: break-word; }
.review .muted, .muted { color: var(--text-muted); }

.ready-note { padding: 8px 0; }

/* Footer */
.wizard-footer { display: flex; gap: 8px; align-items: center; margin-top: 20px; }
.wizard-footer .spacer { flex: 1; }

.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
}
.checkbox-row input { width: auto; margin: 0; }
.ts-field { padding-left: 24px; border-left: 1px solid var(--border); }
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 0; }
.field-error { font-size: 11px; color: var(--red); margin: 2px 0 0; }
</style>
