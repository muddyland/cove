<template>
  <AppShell>
    <div class="page-header">
      <h2>// IMAGE REGISTRY</h2>
      <div class="header-actions">
        <select v-if="zones.hasRemote" v-model.number="zoneId" class="zone-select" @change="onZoneChange">
          <option v-for="z in zones.items" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <NeonButton variant="secondary" :loading="syncing" @click="handleSync"><RefreshCw :size="14" /> Sync LinuxServer</NeonButton>
        <NeonButton variant="primary" @click="showForm = true"><Plus :size="14" /> Add Image</NeonButton>
      </div>
    </div>
    <div v-if="loading" class="empty">LOADING…</div>
    <div v-else-if="!images.length" class="empty">
      No images yet — click “Sync LinuxServer” to import the catalog, or “Add Image”.
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Docker Image</th>
            <th>Type</th>
            <th>Downloaded</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="img in images" :key="img.id">
            <td>
              <span class="name-cell">
                <img v-if="img.logo_url" :src="img.logo_url" class="img-logo" alt="" @error="hideLogo" />
                {{ img.name }}
              </span>
            </td>
            <td><code>{{ img.docker_image }}</code></td>
            <td>{{ img.image_type }}</td>
            <td>
              <span class="pull-state" :class="pullStatus[img.id] ?? 'unknown'">
                <component :is="pullIcon(img.id)" :size="13" :class="{ spin: pullStatus[img.id] === 'pulling' }" />
                {{ pullLabel(img.id) }}
              </span>
            </td>
            <td>
              <span class="status-dot" :class="img.enabled ? 'enabled' : 'disabled'">
                {{ img.enabled ? 'Enabled' : 'Disabled' }}
              </span>
            </td>
            <td class="actions">
              <NeonButton
                variant="ghost"
                :loading="pullStatus[img.id] === 'pulling'"
                @click="handlePull(img)"
              >
                <Download :size="13" /> {{ pullStatus[img.id] === 'present' ? 'Re-pull' : 'Pull' }}
              </NeonButton>
              <NeonButton
                variant="ghost"
                :loading="togglingId === img.id"
                :disabled="togglingId === img.id"
                @click="toggleEnabled(img)"
              >
                <component :is="img.enabled ? ToggleRight : ToggleLeft" :size="15" />
                {{ img.enabled ? 'Disable' : 'Enable' }}
              </NeonButton>
              <NeonButton variant="danger" @click="confirmDelete(img)"><Trash2 :size="13" /> Delete</NeonButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <BaseModal v-model="showForm" title="Add Image">
      <form @submit.prevent="handleAdd" class="form">
        <div class="form-group">
          <label>Display Name</label>
          <input v-model="form.name" required placeholder="Ubuntu KDE" />
        </div>
        <div class="form-group">
          <label>Docker Image</label>
          <input v-model="form.docker_image" required placeholder="lscr.io/linuxserver/webtop:ubuntu-kde" />
        </div>
        <div class="form-group">
          <label>Type</label>
          <select v-model="form.image_type">
            <option value="desktop">Desktop</option>
            <option value="link">Link (browser)</option>
          </select>
        </div>
        <div class="form-group">
          <label>Internal Port (optional)</label>
          <input v-model.number="form.internal_port" type="number" min="1" max="65535" placeholder="3000" />
        </div>
        <div class="form-group">
          <label>Description (optional)</label>
          <input v-model="form.description" />
        </div>
        <div v-if="formError" class="form-error">{{ formError }}</div>
        <div class="form-actions">
          <NeonButton type="button" variant="secondary" @click="showForm = false">Cancel</NeonButton>
          <NeonButton type="submit" variant="primary" :loading="adding">Add</NeonButton>
        </div>
      </form>
    </BaseModal>

    <BaseModal v-model="showConfirm" title="Delete Image">
      <div class="delete-modal">
        <p class="delete-q">
          What should happen to <strong>{{ deleteTarget?.name }}</strong>?
        </p>
        <p class="delete-sub">
          <code>{{ deleteTarget?.docker_image }}</code>
        </p>

        <button
          class="delete-opt"
          :disabled="deleting !== null"
          @click="handleDeleteImageOnly"
        >
          <span class="opt-head">
            <HardDriveDownload :size="15" />
            Delete downloaded image only
            <Loader v-if="deleting === 'image'" :size="14" class="spin opt-load" />
          </span>
          <span class="opt-desc">
            Frees disk space (<code>docker image rm</code>) but keeps this
            catalog entry so you can re-pull it later.
          </span>
        </button>

        <button
          class="delete-opt danger"
          :disabled="deleting !== null"
          @click="handleRemoveEntry"
        >
          <span class="opt-head">
            <Trash2 :size="15" />
            Remove entry &amp; delete image
            <Loader v-if="deleting === 'entry'" :size="14" class="spin opt-load" />
          </span>
          <span class="opt-desc">
            Removes the catalog entry and deletes the downloaded image from disk.
          </span>
        </button>

        <div class="delete-actions">
          <NeonButton variant="secondary" :disabled="deleting !== null" @click="showConfirm = false">Cancel</NeonButton>
        </div>
      </div>
    </BaseModal>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import BaseModal from '@/components/BaseModal.vue'
import {
  RefreshCw, Plus, Trash2, ToggleLeft, ToggleRight,
  Download, CheckCircle2, CircleDashed, Loader, HardDriveDownload,
} from 'lucide-vue-next'
import { imagesApi, type ImagePullStatus } from '@/api/images'
import { useUiStore } from '@/stores/ui'
import { useZonesStore } from '@/stores/zones'
import type { WorkspaceImage } from '@/types'

const images = ref<WorkspaceImage[]>([])
const loading = ref(true)
// Id of the image whose enable/disable toggle is in flight (per-row spinner).
const togglingId = ref<number | null>(null)
const pullStatus = ref<Record<number, ImagePullStatus>>({})
let pullTimer: ReturnType<typeof setInterval> | null = null
const ui = useUiStore()
const zones = useZonesStore()
// The catalog is global; download state and pull/remove actions target this zone
// (0 = local control plane). The selector shows only when remote zones exist.
const zoneId = ref(0)
const showForm = ref(false)
const showConfirm = ref(false)
const deleteTarget = ref<WorkspaceImage | null>(null)
const adding = ref(false)
// null = idle; 'image' / 'entry' marks which delete action is in flight.
const deleting = ref<null | 'image' | 'entry'>(null)
const syncing = ref(false)
const formError = ref('')
const form = reactive({ name: '', docker_image: '', image_type: 'desktop', description: '', internal_port: 3000 })

function hideLogo(e: Event) {
  ;(e.target as HTMLImageElement).style.display = 'none'
}

async function load() { images.value = await imagesApi.list() }

async function loadPullStatus() {
  try {
    pullStatus.value = await imagesApi.pullStatus(zoneId.value)
  } catch {
    // Best-effort; the daemon may be briefly unreachable.
  }
  schedulePullPoll()
}

// Re-query download state against the newly selected zone.
async function onZoneChange() {
  if (pullTimer) { clearInterval(pullTimer); pullTimer = null }
  pullStatus.value = {}
  await loadPullStatus()
}

// Poll only while something is actively downloading.
function schedulePullPoll() {
  const anyPulling = Object.values(pullStatus.value).some(s => s === 'pulling')
  if (anyPulling && !pullTimer) {
    pullTimer = setInterval(loadPullStatus, 3000)
  } else if (!anyPulling && pullTimer) {
    clearInterval(pullTimer)
    pullTimer = null
  }
}

onMounted(async () => {
  await zones.fetch()
  try {
    await load()
  } finally {
    loading.value = false
  }
  await loadPullStatus()
})
onUnmounted(() => { if (pullTimer) clearInterval(pullTimer) })

function pullIcon(id: number) {
  const s = pullStatus.value[id]
  if (s === 'present') return CheckCircle2
  if (s === 'pulling') return Loader
  return CircleDashed
}
function pullLabel(id: number): string {
  const s = pullStatus.value[id]
  if (s === 'present') return 'Downloaded'
  if (s === 'pulling') return 'Pulling…'
  if (s === 'absent') return 'Not pulled'
  return '—'
}

async function handlePull(img: WorkspaceImage) {
  try {
    await imagesApi.pull(img.id, zoneId.value)
    pullStatus.value = { ...pullStatus.value, [img.id]: 'pulling' }
    ui.toast(`Pulling ${img.name}…`, 'info')
    schedulePullPoll()
  } catch (e: any) { ui.toast(e.message, 'error') }
}

async function handleSync() {
  syncing.value = true
  try {
    const res = await imagesApi.sync()
    await load()
    await loadPullStatus()
    ui.toast(`Synced — ${res.added} added, ${res.updated} updated (${res.total} total)`, 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { syncing.value = false }
}

async function handleAdd() {
  formError.value = ''
  adding.value = true
  try {
    const img = await imagesApi.create({
      name: form.name,
      docker_image: form.docker_image,
      image_type: form.image_type,
      description: form.description || undefined,
      internal_port: form.internal_port,
    })
    images.value.push(img)
    showForm.value = false
    await loadPullStatus()
    ui.toast('Image added', 'success')
    Object.assign(form, { name: '', docker_image: '', image_type: 'desktop', description: '', internal_port: 3000 })
  } catch (e: any) { formError.value = e.message }
  finally { adding.value = false }
}

async function toggleEnabled(img: WorkspaceImage) {
  togglingId.value = img.id
  try {
    const updated = await imagesApi.update(img.id, { enabled: !img.enabled })
    const idx = images.value.findIndex(i => i.id === img.id)
    if (idx !== -1) images.value[idx] = updated
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { togglingId.value = null }
}

function confirmDelete(img: WorkspaceImage) { deleteTarget.value = img; showConfirm.value = true }

// Delete the downloaded image only; keep the catalog entry (now "Not pulled").
async function handleDeleteImageOnly() {
  if (!deleteTarget.value) return
  const id = deleteTarget.value.id
  deleting.value = 'image'
  try {
    await imagesApi.removeImageOnly(id, zoneId.value)
    pullStatus.value = { ...pullStatus.value, [id]: 'absent' }
    showConfirm.value = false
    ui.toast('Downloaded image deleted', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { deleting.value = null }
}

// Remove the catalog entry and delete the downloaded image from disk.
async function handleRemoveEntry() {
  if (!deleteTarget.value) return
  const id = deleteTarget.value.id
  deleting.value = 'entry'
  try {
    await imagesApi.remove(id, true, zoneId.value)
    images.value = images.value.filter(i => i.id !== id)
    showConfirm.value = false
    ui.toast('Image entry removed', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { deleting.value = null }
}
</script>

<style scoped>
@import '@/styles/tables.css';
.header-actions { display: flex; gap: 8px; align-items: center; }
.zone-select {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text); font-family: var(--font-mono); font-size: 12px; padding: 6px 8px;
}
.name-cell { display: inline-flex; align-items: center; gap: 8px; }
.img-logo { width: 20px; height: 20px; border-radius: var(--radius-sm); object-fit: cover; flex-shrink: 0; }
.status-dot { font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px; }
.enabled { color: var(--green); text-shadow: 0 0 6px var(--green); }
.disabled { color: var(--text-muted); }
.pull-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.pull-state.present { color: var(--green); }
.pull-state.pulling { color: var(--accent); }
.pull-state.absent, .pull-state.unknown { color: var(--text-muted); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.form { display: flex; flex-direction: column; gap: 14px; }
.form-actions { display: flex; gap: 8px; justify-content: flex-end; }

/* Delete-image modal: two stacked choice cards. */
.delete-modal { display: flex; flex-direction: column; gap: 14px; }
.delete-q { margin: 0; font-size: 14px; color: var(--text); }
.delete-q strong { color: var(--accent); }
.delete-sub { margin: -8px 0 2px; }
.delete-sub code { font-size: 11px; color: var(--text-muted); }
.delete-opt {
  display: flex; flex-direction: column; gap: 6px;
  text-align: left; width: 100%;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 14px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.delete-opt:hover:not(:disabled) { border-color: var(--accent); box-shadow: var(--glow-sm); }
.delete-opt.danger:hover:not(:disabled) { border-color: var(--red); box-shadow: 0 0 8px rgba(255, 32, 85, 0.3); }
.delete-opt:disabled { opacity: 0.5; cursor: default; }
.opt-head {
  display: flex; align-items: center; gap: 8px;
  font-family: var(--font-mono); font-size: 12px; font-weight: 600;
  letter-spacing: 0.5px; color: var(--text);
}
.delete-opt.danger .opt-head { color: var(--red); }
.opt-load { margin-left: auto; }
.opt-desc { font-size: 11px; color: var(--text-muted); line-height: 1.5; }
.opt-desc code { font-size: 10px; }
.delete-actions { display: flex; justify-content: flex-end; }
</style>
