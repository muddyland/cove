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

    <!-- Step 1: What to launch -->
    <section v-if="step === 'kind'" class="wizard-step">
      <div class="kinds" role="listbox" aria-label="What to launch">
        <button
          v-for="k in KINDS"
          :key="k.value"
          type="button"
          class="kind-card"
          role="option"
          :aria-selected="kind === k.value"
          :class="{ selected: kind === k.value, empty: !countFor(k.value) }"
          @click="pickKind(k.value)"
        >
          <component :is="k.icon" :size="22" class="kind-icon" />
          <span class="kind-name">{{ k.label }}</span>
          <span class="kind-desc">{{ k.desc }}</span>
          <span class="kind-count">{{ countFor(k.value) }} image{{ countFor(k.value) === 1 ? '' : 's' }}</span>
        </button>
      </div>
      <p v-if="!images.length" class="hint empty">
        No images yet — an admin can add them from Admin → Images.
      </p>
    </section>

    <!-- Step 2: Choose an image -->
    <section v-else-if="step === 'choose'" class="wizard-step">
      <div class="chooser-controls">
        <input v-model="search" class="search" type="search" placeholder="Search images…" aria-label="Search images" />
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
            <span class="card-type" :class="img.image_type">
              <component :is="typeIcon(img.image_type)" :size="11" />{{ img.image_type }}
            </span>
            <span v-if="img.description" class="card-desc">{{ img.description }}</span>
            <span v-if="availability[img.id] === 'absent'" class="card-pull" title="Not downloaded on this zone yet — the first start downloads it (often several GB)">
              <Download :size="10" /> not downloaded
            </span>
            <span v-else-if="availability[img.id] === 'pulling'" class="card-pull">
              <Download :size="10" /> downloading…
            </span>
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

      <p v-if="noZones" class="field-error">
        Your account isn't allowed to run workspaces on any zone. Ask an admin to allow one under Admin → Users.
      </p>
      <div v-if="zonesStore.hasRemote" class="form-group">
        <label>Zone</label>
        <select v-model.number="form.zone_id">
          <option v-for="z in zonesStore.items" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <p class="hint">Which zone (host) runs this workspace. Local is this Cove host.</p>
      </div>

      <div v-if="urlCapable" class="form-group">
        <label>{{ urlRequired ? 'Target URL(s)' : 'Open URL(s) (optional)' }}</label>
        <textarea v-model="form.target_url" rows="2" placeholder="https://example.com" :required="urlRequired" />
        <p class="hint">One URL per line — each opens in its own tab (up to 6).</p>
        <p v-if="showError('url')" class="field-error">A target URL is required for this image.</p>
      </div>
      <ToggleRow v-if="urlCapable && browserFeatures.dark" v-model="form.kiosk_dark" @update:model-value="darkTouched = true">
        Dark mode
        <template v-if="darkFollowsBrowser" #hint>
          On, matching this browser. Pages without a dark theme are rendered dark.
        </template>
      </ToggleRow>
      <ToggleRow v-if="showKiosk" v-model="form.kiosk">
        {{ browserFeatures.kiosk ? 'Kiosk mode (full-screen, no browser chrome)' : 'Start full-screen' }}
      </ToggleRow>
      <p v-if="urlCapable && urlCount > 1 && browserFeatures.fullscreen" class="hint">
        Multiple tabs open full-screen with a tab bar — kiosk lock is unavailable (it would hide the tabs).
      </p>
      <template v-if="showKiosk && form.kiosk && browserFeatures.kiosk && browserFeatures.fullscreen">
        <div class="ts-field toggles">
          <ToggleRow v-model="form.kiosk_menu">Allow right-click / refresh menu</ToggleRow>
        </div>
      </template>
      <p v-if="urlCapable && !browserFeatures.kiosk && !browserFeatures.fullscreen" class="hint">
        {{ selectedImage?.name }} ignores start-up window flags, so it opens in its normal window.
      </p>
      <template v-if="urlCapable">
        <ToggleRow v-model="form.ephemeral">
          Ephemeral (no saved data — wiped when stopped)
          <template v-if="form.ephemeral" #hint>
            Cookies, history, and downloads live only in the container and are discarded when it stops.
          </template>
        </ToggleRow>
        <div v-if="form.ephemeral" class="ts-field toggles">
          <ToggleRow v-model="form.auto_remove">
            Discard when stopped (like <code>docker run --rm</code>)
            <template v-if="form.auto_remove" #hint>
              The workspace disappears from the grid when it stops, instead of leaving a card that can only start blank.
            </template>
          </ToggleRow>
        </div>
      </template>

      <p v-if="selectedAvailability === 'absent'" class="notice">
        <Download :size="13" />
        <span><strong>{{ selectedImage?.name }}</strong> isn't downloaded on {{ zoneName }} yet. The first
        start downloads it, which can take several minutes — progress shows on the card.</span>
      </p>

      <p v-if="!urlCapable && !noZones" class="hint ready-note">Ready to launch — or customize networking &amp; apps below.</p>
    </section>

    <!-- Step 3: Network -->
    <section v-else-if="step === 'network'" class="wizard-step form">
      <NetworkFields
        :form="form"
        :lan-policy="lanPolicy"
        :tailscale-profiles="tsProfiles"
        :vpn-profiles="vpnProfiles"
        :dns-error="dnsErr"
      />
    </section>

    <!-- Step 4: Access (permissions + streaming) -->
    <section v-else-if="step === 'access'" class="wizard-step form">
      <AccessFields
        :form="form"
        :gpu-enabled="gpuPolicy.enabled"
        :show-browser-lock="urlCapable"
        :permissions="!urlCapable"
      />
    </section>

    <!-- Step 5: Apps -->
    <section v-else-if="step === 'apps'" class="wizard-step form">
      <AppsFields
        :form="form"
        :docker-enabled="dockerPolicy.enabled"
        :pkg-error="pkgErr"
        :app-image-error="appImageErr"
        :launchers="launchersAvailable"
      />
    </section>

    <!-- Step 4: Review -->
    <section v-else-if="step === 'review'" class="wizard-step">
      <dl class="review">
        <div><dt><component :is="typeIcon(selectedImage?.image_type)" :size="12" />Image</dt><dd>{{ selectedImage?.name }} <span class="muted">({{ selectedImage?.image_type }})</span></dd></div>
        <div><dt><Tag :size="12" />Name</dt><dd>{{ form.name || '—' }}</dd></div>
        <div v-if="zonesStore.hasRemote"><dt><Server :size="12" />Zone</dt><dd>{{ zoneName }}</dd></div>
        <div v-if="urlCapable && form.target_url.trim()"><dt><Link2 :size="12" />URL(s)</dt><dd>{{ form.target_url.trim() }}</dd></div>
        <div><dt><Network :size="12" />Network</dt><dd>{{ networkSummary }}</dd></div>
        <div><dt><ShieldCheck :size="12" />Access</dt><dd>{{ accessSummary }}</dd></div>
        <div v-if="appsSummary"><dt><Package :size="12" />Apps</dt><dd>{{ appsSummary }}</dd></div>
        <div v-if="selectedAvailability === 'absent'"><dt><Download :size="12" />Image</dt><dd>Downloaded on first start (can take minutes)</dd></div>
      </dl>
      <p class="hint">SSH key injection and Wayland streaming are on by default. You can change these under Customize.</p>
    </section>

    <div v-if="error" class="form-error">{{ error }}</div>

    <!-- Footer navigation -->
    <div class="wizard-footer">
      <NeonButton v-if="step !== 'kind'" type="button" variant="secondary" @click="goBack">Back</NeonButton>
      <span class="spacer" />
      <template v-if="step === 'kind'" />
      <template v-else-if="step === 'choose'">
        <NeonButton type="button" variant="primary" :disabled="!form.image_id" @click="goNext">Next</NeonButton>
      </template>
      <template v-else-if="step === 'basics'">
        <NeonButton type="button" variant="secondary" :disabled="noZones" @click="goToNetwork">Customize →</NeonButton>
        <NeonButton type="button" variant="primary" :loading="loading" :disabled="noZones" @click="launch">Launch</NeonButton>
      </template>
      <template v-else-if="step === 'network'">
        <NeonButton type="button" variant="secondary" @click="step = 'access'">Next: Access →</NeonButton>
        <NeonButton type="button" variant="primary" :loading="loading" :disabled="noZones" @click="launch">Launch</NeonButton>
      </template>
      <template v-else-if="step === 'access'">
        <NeonButton type="button" variant="secondary" @click="step = appsStepAvailable ? 'apps' : 'review'">
          {{ appsStepAvailable ? 'Next: Apps →' : 'Review →' }}
        </NeonButton>
        <NeonButton type="button" variant="primary" :loading="loading" :disabled="noZones" @click="launch">Launch</NeonButton>
      </template>
      <template v-else-if="step === 'apps'">
        <NeonButton type="button" variant="secondary" @click="step = 'review'">Review →</NeonButton>
        <NeonButton type="button" variant="primary" :loading="loading" :disabled="noZones" @click="launch">Launch</NeonButton>
      </template>
      <template v-else>
        <NeonButton type="button" variant="primary" :loading="loading" :disabled="noZones" @click="launch">Launch</NeonButton>
      </template>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted } from 'vue'
import BaseModal from './BaseModal.vue'
import ToggleRow from './ToggleRow.vue'
import NeonButton from './NeonButton.vue'
import NetworkFields from './NetworkFields.vue'
import AccessFields from './AccessFields.vue'
import AppsFields from './AppsFields.vue'
import { Monitor, Globe, AppWindow, Link2, Tag, Server, Network, ShieldCheck, Package, Download } from 'lucide-vue-next'
import { dnsError, packagesError, appImagesError } from '@/utils/workspaceForm'
import { imagesApi, type ImagePullStatus } from '@/api/images'
import { workspacesApi } from '@/api/workspaces'
import { usersApi } from '@/api/users'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useZonesStore } from '@/stores/zones'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type {
  DockerPolicy, GpuPolicy, ImageType, LanPolicy, TailscaleProfile, VpnProfile, WorkspaceImage,
} from '@/types'

const open = defineModel<boolean>({ default: false })

type StepKey = 'kind' | 'choose' | 'basics' | 'network' | 'access' | 'apps' | 'review'
const STEPS: { key: StepKey; label: string }[] = [
  { key: 'kind', label: 'Launch' },
  { key: 'choose', label: 'Choose' },
  { key: 'basics', label: 'Set up' },
  { key: 'network', label: 'Network' },
  { key: 'access', label: 'Access' },
  { key: 'apps', label: 'Apps' },
  { key: 'review', label: 'Review' },
]
const step = ref<StepKey>('kind')
// The linear "early launch" path skips the Network/Access/Apps steps; they only
// appear once the user opts into Customize, so the rail shows either 3 or 6 steps.
const CUSTOMIZE_STEPS = new Set<StepKey>(['network', 'access', 'apps'])
const visibleSteps = computed(() =>
  STEPS.filter(s => {
    if (CUSTOMIZE_STEPS.has(s.key) && !customizing.value) return false
    if (s.key === 'apps' && !appsStepAvailable.value) return false
    return true
  }),
)
const customizing = ref(false)
function stepIndex(k: StepKey) {
  return visibleSteps.value.findIndex(s => s.key === k)
}

const images = ref<WorkspaceImage[]>([])
const lanPolicy = ref<LanPolicy>({ enabled: false, subnets: [] })
const gpuPolicy = ref<GpuPolicy>({ enabled: false })
const dockerPolicy = ref<DockerPolicy>({ enabled: false })
const tsProfiles = ref<TailscaleProfile[]>([])
const vpnProfiles = ref<VpnProfile[]>([])
const loading = ref(false)
const error = ref('')
const submitted = ref(false)
const nameEdited = ref(false)
const search = ref('')

// What to launch, asked before anything else: it decides which images are worth
// showing and which questions the rest of the wizard needs to ask.
type Kind = 'desktop' | 'browser' | 'app'
const KINDS: { value: Kind; label: string; desc: string; icon: any }[] = [
  { value: 'desktop', label: 'Desktop', desc: 'A full Linux desktop you can install things into.', icon: Monitor },
  { value: 'browser', label: 'Browser', desc: 'A browser, optionally opening straight to a site.', icon: Globe },
  { value: 'app', label: 'App', desc: 'One application streamed on its own (VSCodium, GIMP…).', icon: AppWindow },
]
const kind = ref<Kind>('desktop')

// Dark mode starts on when this browser is dark: opening a link from a dark
// browser into a workspace that flashes white is what that avoids. The launcher
// extension does the same. Touching the toggle makes it your choice.
const prefersDark = typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches
const darkTouched = ref(false)
const darkFollowsBrowser = computed(() => !darkTouched.value && prefersDark && form.kiosk_dark)
// "Link" images are the legacy URL type; they belong with the browsers.
const kindOf = (t: ImageType) => (t === 'link' ? 'browser' : t)
function countFor(k: Kind) {
  return images.value.filter(i => kindOf(i.image_type) === k).length
}
function pickKind(k: Kind) {
  if (kind.value !== k) {
    kind.value = k
    form.image_id = ''
  }
  step.value = 'choose'
}

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
  kiosk_dark: prefersDark,
  kiosk_menu: false,
  use_tailscale: false,
  use_gluetun: false,
  tailscale_profile_id: null as number | null,
  gluetun_profile_id: null as number | null,
  ephemeral: false,
  auto_remove: false,
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
  shared_profile: false,
  install_packages: '',
  proot_apps: [] as string[],
  appimages: '',
})

function typeIcon(t?: ImageType) {
  return t === 'browser' ? Globe : t === 'app' ? AppWindow : t === 'link' ? Link2 : Monitor
}

const selectedImage = computed(() => images.value.find(i => i.id === form.image_id))
const urlCapable = computed(() =>
  !!selectedImage.value && (!!selectedImage.value.url_env || selectedImage.value.image_type === 'link'),
)
const urlRequired = computed(() => selectedImage.value?.image_type === 'link')
const urlCount = computed(() => form.target_url.trim().split(/\s+/).filter(Boolean).length)
// Package installs / proot-apps / AppImages / Docker-in-Docker only apply to a
// full desktop — hide the whole Apps step for browser, link, and single-app images.
// Packages and Docker work on desktops and single-app images; proot-apps and
// AppImages only add desktop menu launchers, so they need a full desktop.
const appsStepAvailable = computed(() => ['desktop', 'app'].includes(selectedImage.value?.image_type ?? ''))
const launchersAvailable = computed(() => selectedImage.value?.image_type === 'desktop')

// Inline validation (owned here so it's available whichever step is mounted).
const dnsErr = computed(() => dnsError(form.custom_dns, form.dns_servers))
const pkgErr = computed(() => packagesError(form.install_packages))
const appImageErr = computed(() => appImagesError(form.appimages))

const filteredImages = computed(() => {
  const q = search.value.trim().toLowerCase()
  return images.value.filter(img => {
    if (kindOf(img.image_type) !== kind.value) return false
    if (!q) return true
    return (
      img.name.toLowerCase().includes(q) ||
      (img.description ?? '').toLowerCase().includes(q)
    )
  })
})

const zoneName = computed(() => zonesStore.items.find(z => z.id === form.zone_id)?.name ?? 'Local')

// Whether each image is already on the chosen zone, so a multi-GB first pull is
// expected rather than looking like a hung start. Best-effort: empty on error.
const availability = ref<Record<number, ImagePullStatus>>({})
async function loadAvailability() {
  try {
    availability.value = await imagesApi.availability(form.zone_id)
  } catch {
    availability.value = {}
  }
}
watch(() => form.zone_id, loadAvailability)

// GPU and Docker-in-Docker are per zone (each host has its own GPU, and DinD
// is opted in zone by zone). A choice the new zone doesn't offer is cleared:
// a hidden-but-set flag would fail the launch over a control the user can't see.
async function loadZonePolicies() {
  const zoneId = form.zone_id
  const [gpu, docker] = await Promise.all([
    workspacesApi.gpuPolicy(zoneId).catch(() => ({ enabled: false })),
    workspacesApi.dockerPolicy(zoneId).catch(() => ({ enabled: false })),
  ])
  if (zoneId !== form.zone_id) return // the user picked another zone meanwhile
  gpuPolicy.value = gpu
  dockerPolicy.value = docker
  if (!gpu.enabled) form.gpu_accel = false
  if (!docker.enabled) form.use_docker = false
}
watch(() => form.zone_id, loadZonePolicies)
watch(open, (o) => { if (o) loadAvailability() })
const selectedAvailability = computed(() =>
  typeof form.image_id === 'number' ? availability.value[form.image_id] : undefined,
)

// Which start-up options this browser actually honours — the server works them
// out per image (see catalog.browser_features), so an option that would be
// ignored is never offered.
const browserFeatures = computed(
  () => selectedImage.value?.browser ?? { kiosk: false, fullscreen: false, dark: false },
)
const showKiosk = computed(
  () => urlCapable.value && urlCount.value <= 1 && (browserFeatures.value.kiosk || browserFeatures.value.fullscreen),
)

// A workspace on a remote zone keeps nothing on the agent's storage, so it is
// always ephemeral (the old Open Website flow did the same).
const forcedEphemeral = computed(() => urlCapable.value && form.zone_id !== 0)
const networkSummary = computed(() => {
  if (form.use_tailscale) {
    const p = tsProfiles.value.find(t => t.id === form.tailscale_profile_id)
    const bits = [p?.name, form.ts_exit_node ? `exit: ${form.ts_exit_node}` : ''].filter(Boolean)
    return 'Tailscale' + (bits.length ? ` (${bits.join(', ')})` : '')
  }
  if (form.use_gluetun) {
    const p = vpnProfiles.value.find(v => v.id === form.gluetun_profile_id)
    return 'VPN' + (p ? ` (${p.name})` : '')
  }
  if (form.custom_dns && form.dns_servers.trim()) return `Custom DNS (${form.dns_servers.trim()})`
  return 'Direct' + (form.lan_access ? ' + LAN' : '')
})
const accessSummary = computed(() => {
  const parts: string[] = []
  if (!urlCapable.value) {
    parts.push(form.inject_ssh_key ? 'SSH key' : 'no SSH key')
    if (form.allow_sudo) parts.push('sudo')
  }
  if (form.gpu_accel) parts.push('GPU')
  if (form.use_docker) parts.push('Docker')
  if (form.shared_profile) parts.push('shared profile')
  return parts.join(', ')
})
const appsSummary = computed(() => {
  const parts: string[] = []
  if (launchersAvailable.value && form.proot_apps.length) parts.push(`${form.proot_apps.length} proot-app(s)`)
  if (form.install_packages.trim()) parts.push('packages')
  if (launchersAvailable.value && form.appimages.trim()) parts.push('AppImages')
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
function goToNetwork() {
  customizing.value = true
  step.value = 'network'
}
function goBack() {
  if (step.value === 'choose') step.value = 'kind'
  else if (step.value === 'basics') step.value = 'choose'
  else if (step.value === 'network') step.value = 'basics'
  else if (step.value === 'access') step.value = 'network'
  else if (step.value === 'apps') step.value = 'access'
  else if (step.value === 'review') {
    step.value = customizing.value ? (appsStepAvailable.value ? 'apps' : 'access') : 'basics'
  }
}

// The zones this user may use (an admin can limit them). Default to Local when
// it's allowed, else the first allowed zone, so a launch never targets a zone
// the server will refuse.
const noZones = computed(() => zonesStore.loaded && !zonesStore.items.length)
function defaultZone(): number {
  const items = zonesStore.items
  return !items.length || items.some(z => z.id === 0) ? 0 : items[0].id
}
function ensureAllowedZone() {
  if (zonesStore.loaded && !zonesStore.items.some(z => z.id === form.zone_id)) form.zone_id = defaultZone()
}

onMounted(async () => {
  zonesStore.fetch().then(ensureAllowedZone)
  images.value = await imagesApi.list()
  loadAvailability()
  try {
    lanPolicy.value = await workspacesApi.lanPolicy()
  } catch {
    // Non-fatal: the LAN checkbox just stays hidden if the policy can't load.
  }
  await loadZonePolicies()
  // Non-fatal: without profiles the Tailscale / VPN segments stay disabled.
  usersApi.listVpnProfiles().then(v => { vpnProfiles.value = v }).catch(() => {})
  usersApi.listTailscaleProfiles().then(t => { tsProfiles.value = t }).catch(() => {})
})

function resetForm() {
  step.value = 'kind'
  customizing.value = false
  submitted.value = false
  nameEdited.value = false
  search.value = ''
  kind.value = 'desktop'
  Object.assign(form, {
    name: '', image_id: '', zone_id: defaultZone(), target_url: '',
    kiosk: false, kiosk_dark: prefersDark, kiosk_menu: false,
    use_tailscale: false, use_gluetun: false, tailscale_profile_id: null, gluetun_profile_id: null,
    ephemeral: false, auto_remove: false, lan_access: false,
    ts_exit_node: '', ts_accept_routes: true, ts_accept_dns: true,
    custom_dns: false, dns_servers: '', allow_sudo: false, inject_ssh_key: true,
    pixelflux_wayland: true, clear_browser_lock: false, gpu_accel: false,
    use_docker: false, shared_profile: false, install_packages: '', proot_apps: [], appimages: '',
  })
}


// "Discard when stopped" only exists for ephemeral workspaces — the API refuses
// the pair otherwise, since a persistent home would be destroyed on an ordinary
// stop. The checkbox is hidden when ephemeral is off, so clear the value too:
// a hidden-but-set flag would fail the launch with an error about a control the
// user cannot see.
watch(() => form.ephemeral, (on) => { if (!on) form.auto_remove = false })

async function launch() {
  submitted.value = true
  // Guard the required fields (name + image, and URL for link images) so we don't
  // bounce off a server 422; jump the user to the offending step.
  if (!form.image_id) { step.value = 'choose'; return }
  if (noZones.value) { step.value = 'basics'; return }
  if (!form.name.trim() || (urlRequired.value && !form.target_url.trim())) { step.value = 'basics'; return }
  if (dnsErr.value) { customizing.value = true; step.value = 'network'; return }
  if (appsStepAvailable.value && (pkgErr.value || (launchersAvailable.value && appImageErr.value))) {
    customizing.value = true; step.value = 'apps'; return
  }
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
      // Independent of kiosk: forcing dark pages has nothing to do with the window.
      kiosk_dark: urlCapable.value && browserFeatures.value.dark ? form.kiosk_dark : false,
      kiosk_menu: urlCapable.value && urlCount.value <= 1 && form.kiosk ? form.kiosk_menu : false,
      use_tailscale: form.use_tailscale,
      use_gluetun: form.use_gluetun,
      ...(form.use_tailscale && form.tailscale_profile_id != null ? { tailscale_profile_id: form.tailscale_profile_id } : {}),
      ...(form.use_gluetun && form.gluetun_profile_id != null ? { gluetun_profile_id: form.gluetun_profile_id } : {}),
      ephemeral: urlCapable.value ? (forcedEphemeral.value || form.ephemeral) : false,
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
      allow_sudo: !urlCapable.value && form.allow_sudo,
      inject_ssh_key: !urlCapable.value && form.inject_ssh_key,
      pixelflux_wayland: form.pixelflux_wayland,
      clear_browser_lock: form.clear_browser_lock,
      gpu_accel: form.gpu_accel,
      use_docker: form.use_docker,
      shared_profile: form.shared_profile,
      ...(form.install_packages.trim() ? { install_packages: form.install_packages.trim() } : {}),
      ...(launchersAvailable.value && form.proot_apps.length ? { proot_apps: form.proot_apps.join(' ') } : {}),
      ...(launchersAvailable.value && form.appimages.trim() ? { appimages: form.appimages.trim() } : {}),
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
  color: var(--text-muted); font-size: 12px; letter-spacing: 0.5px;
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
/* Phones: room for only one label — keep the current step's, numbers for the rest. */
@media (max-width: 560px) {
  .steps li { flex: 0 0 auto; }
  .steps li.active { flex: 1 1 auto; }
  .steps li:not(.active) .step-label { display: none; }
  .steps li:not(:last-child)::after { width: 10px; flex: 0 0 10px; }
  .steps li.active:not(:last-child)::after { flex: 1; }
}
.card-pull {
  display: inline-flex; align-items: center; gap: 4px;
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 0.5px; color: var(--amber);
}
.notice {
  display: flex; gap: 8px; align-items: flex-start;
  font-size: 13px; line-height: 1.5; color: var(--text);
  background: rgba(255, 170, 0, 0.06); border: 1px solid rgba(255, 170, 0, 0.25);
  border-radius: var(--radius-sm); padding: 8px 10px;
}
.notice svg { color: var(--amber); flex-shrink: 0; margin-top: 3px; }

/* Chooser */
.kinds { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.kind-card {
  display: flex; flex-direction: column; align-items: flex-start; gap: 6px;
  padding: 16px; cursor: pointer; text-align: left;
  background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text); transition: all 0.15s;
}
.kind-card:hover { border-color: var(--accent); }
.kind-card.selected { border-color: var(--accent); box-shadow: var(--glow-sm); }
.kind-card.empty { opacity: 0.55; }
.kind-icon { color: var(--accent); }
.kind-name { font-family: var(--font-display); font-size: 13px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; }
.kind-desc { font-size: 12px; line-height: 1.45; color: var(--text-muted); }
.kind-count { font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px; color: var(--text-muted); }

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
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; letter-spacing: 1px; text-transform: uppercase;
  color: var(--text-muted); width: fit-content;
}
.card-type.desktop { color: var(--accent); }
.card-type.app { color: #7fe081; }
.card-type.browser { color: var(--pink, #ff00aa); }
.card-desc {
  font-size: 11px; color: var(--text-muted); line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.empty { padding: 24px 0; text-align: center; }

/* Review */
.review { display: flex; flex-direction: column; gap: 10px; margin: 0 0 14px; }
.review > div { display: grid; grid-template-columns: 90px 1fr; gap: 10px; align-items: baseline; }
.review dt {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted); margin: 0;
}
.review dd { margin: 0; font-size: 13px; color: var(--text); word-break: break-word; }
.review .muted, .muted { color: var(--text-muted); }

.ready-note { padding: 8px 0; }

/* Footer */
.wizard-footer { display: flex; gap: 8px; align-items: center; margin-top: 20px; }
.wizard-footer .spacer { flex: 1; }

.ts-field { padding-left: 24px; border-left: 1px solid var(--border); }
.toggles { display: flex; flex-direction: column; gap: 8px; }
.hint { font-size: 12px; line-height: 1.5; color: var(--text-muted); margin: 0; }
.field-error { font-size: 12px; color: var(--red); margin: 2px 0 0; }
</style>
