<template>
  <AppShell>
    <div class="page-header">
      <h2>// IMAGE REGISTRY</h2>
      <div class="header-actions">
        <select v-if="zones.hasRemote" v-model.number="zoneId" class="zone-select" @change="onZoneChange">
          <option v-for="z in zones.items" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <NeonButton variant="secondary" :loading="syncing" @click="handleSync"><RefreshCw :size="14" /> Sync LinuxServer</NeonButton>
        <NeonButton variant="secondary" :loading="resetting" @click="showReset = true"><RotateCcw :size="14" /> Reset Catalog</NeonButton>
        <NeonButton variant="primary" @click="openAdd"><Plus :size="14" /> Add Image</NeonButton>
      </div>
    </div>
    <LoadingSpinner v-if="loading" block />
    <div v-else-if="!images.length" class="empty">
      No images yet — click “Sync LinuxServer” to import the catalog, or “Add Image”.
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name / image</th>
            <th>Type</th>
            <th>On {{ zoneLabel }}</th>
            <th>Used by</th>
            <th>Status</th>
            <th><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="img in images" :key="img.id" :class="{ 'row-disabled': !img.enabled }" @contextmenu="rowMenus.get(img.id)?.openAt($event)">
            <td>
              <span class="name-cell">
                <img v-if="img.logo_url" :src="img.logo_url" class="img-logo" alt="" @error="hideLogo" />
                <span class="name-stack">
                  <span>{{ img.name }}</span>
                  <code class="ref">{{ img.docker_image }}</code>
                </span>
              </span>
            </td>
            <td>{{ img.image_type }}</td>
            <td>
              <span class="pull-state" :class="stateOf(img.id)">
                <component :is="pullIcon(img.id)" :size="13" :class="{ spin: stateOf(img.id) === 'pulling' }" />
                {{ pullLabel(img.id) }}
              </span>
              <span v-if="details[img.id]?.updated_at && stateOf(img.id) === 'present'" class="sub" :title="`Last pulled ${absoluteTime(details[img.id]?.updated_at)}`">
                pulled {{ relativeTime(details[img.id]?.updated_at) }}
              </span>
              <span v-if="updates[img.id]" class="sub" :class="updateClass(img.id)" :title="updates[img.id]?.detail || ''">
                {{ updateLabel(img.id) }}
              </span>
            </td>
            <td :title="usedByTitle(img.id)">{{ details[img.id]?.workspace_count ?? '—' }}</td>
            <td>
              <span class="status-dot" :class="img.enabled ? 'enabled' : 'disabled'">
                {{ img.enabled ? 'Enabled' : 'Disabled' }}
              </span>
            </td>
            <td class="actions">
              <NeonButton
                variant="ghost"
                :loading="stateOf(img.id) === 'pulling'"
                :title="stateOf(img.id) === 'present' ? 'Pull the latest version of this tag' : 'Download now so the first launch is fast'"
                @click="handlePull(img)"
              >
                <Download :size="13" /> {{ stateOf(img.id) === 'present' ? 'Update' : 'Pull' }}
              </NeonButton>
              <RowMenu
                :ref="el => setRowMenu(img.id, el)"
                :items="imageMenu(img)"
                :label="`More actions for ${img.name}`"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <BaseModal v-model="showForm" :title="editingId ? 'Edit Image' : 'Add Image'">
      <form @submit.prevent="handleSubmit" class="form">
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
            <option value="app">App (single Selkies GUI app)</option>
            <option value="browser">Browser</option>
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
        <div v-if="editingId" class="form-group">
          <label>Logo URL (optional)</label>
          <input v-model="form.logo_url" placeholder="https://…/logo.png" />
        </div>
        <div v-if="formError" class="form-error">{{ formError }}</div>
        <div class="form-actions">
          <NeonButton type="button" variant="secondary" @click="showForm = false">Cancel</NeonButton>
          <NeonButton type="submit" variant="primary" :loading="adding">{{ editingId ? 'Save' : 'Add' }}</NeonButton>
        </div>
      </form>
    </BaseModal>

    <BaseModal v-model="showReset" title="Reset Catalog">
      <div class="reset-modal">
        <p class="reset-q">Re-apply curated catalog defaults?</p>
        <p class="reset-desc">
          This re-fetches the LinuxServer catalog, adds any missing images, and
          <strong>forces curated images back to their default type, port, and
          launch settings</strong> — correcting entries that drifted from an
          older seed (e.g. an app stuck as a desktop). Your manually-added images
          and enable/disable choices are left untouched.
        </p>
        <div class="form-actions">
          <NeonButton variant="secondary" :disabled="resetting" @click="showReset = false">Cancel</NeonButton>
          <NeonButton variant="primary" :loading="resetting" @click="handleReset">Reset to defaults</NeonButton>
        </div>
      </div>
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
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import NeonButton from '@/components/NeonButton.vue'
import BaseModal from '@/components/BaseModal.vue'
import RowMenu, { type RowMenuItem } from '@/components/RowMenu.vue'
import {
  RefreshCw, RotateCcw, Plus, Pencil, Trash2, ToggleLeft, ToggleRight,
  Download, CheckCircle2, CircleDashed, Loader, HardDriveDownload, SearchCheck,
} from 'lucide-vue-next'
import { imagesApi, type ImagePullStatus } from '@/api/images'
import { absoluteTime, fmtBytes, relativeTime } from '@/utils/workspaceStatus'
import { useUiStore } from '@/stores/ui'
import { useZonesStore } from '@/stores/zones'
import type { ImageDetail, ImageUpdateCheck, WorkspaceImage, ImageType } from '@/types'

const images = ref<WorkspaceImage[]>([])
const loading = ref(true)
// Id of the image whose enable/disable toggle is in flight (per-row spinner).
const togglingId = ref<number | null>(null)
// Per-image state on the selected zone: pulled?, size, last pulled, usage,
// live download progress (from /images/details).
const details = ref<Record<number, ImageDetail>>({})
// Optimistic overrides between clicking Pull and the next details poll.
const pending = ref<Record<number, ImagePullStatus>>({})
const updates = ref<Record<number, ImageUpdateCheck>>({})
const checkingId = ref<number | null>(null)
const zoneLabel = computed(() => zones.items.find(z => z.id === zoneId.value)?.name ?? 'Local')
let pullTimer: ReturnType<typeof setInterval> | null = null
const ui = useUiStore()
const zones = useZonesStore()
// The catalog is global; download state and pull/remove actions target this zone
// (0 = local control plane). The selector shows only when remote zones exist.
const zoneId = ref(0)
const showForm = ref(false)
const showConfirm = ref(false)
const showReset = ref(false)
const deleteTarget = ref<WorkspaceImage | null>(null)
// null = adding a new image; an id = editing that existing image.
const editingId = ref<number | null>(null)
const adding = ref(false)
// null = idle; 'image' / 'entry' marks which delete action is in flight.
const deleting = ref<null | 'image' | 'entry'>(null)
const syncing = ref(false)
const resetting = ref(false)
const formError = ref('')
const form = reactive({ name: '', docker_image: '', image_type: 'desktop', description: '', internal_port: 3000, logo_url: '' })

const EMPTY_FORM = { name: '', docker_image: '', image_type: 'desktop', description: '', internal_port: 3000, logo_url: '' }

function hideLogo(e: Event) {
  ;(e.target as HTMLImageElement).style.display = 'none'
}

async function load() { images.value = await imagesApi.list() }

async function loadPullStatus() {
  try {
    details.value = await imagesApi.details(zoneId.value)
    pending.value = {}
  } catch {
    // Best-effort; the daemon may be briefly unreachable.
  }
  schedulePullPoll()
}

// Re-query download state against the newly selected zone.
async function onZoneChange() {
  if (pullTimer) { clearInterval(pullTimer); pullTimer = null }
  details.value = {}
  updates.value = {}
  await loadPullStatus()
}

function stateOf(id: number): ImagePullStatus | 'unknown' {
  return pending.value[id] ?? details.value[id]?.status ?? 'unknown'
}

// Poll only while something is actively downloading.
function schedulePullPoll() {
  const anyPulling = Object.keys(details.value).some(id => stateOf(Number(id)) === 'pulling')
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

// ⋯ menu per row (also opened by right-clicking the row).
type RowMenuHandle = { openAt: (e: MouseEvent) => void }
const rowMenus = new Map<number, RowMenuHandle>()
function setRowMenu(id: number, el: unknown) {
  if (el) rowMenus.set(id, el as RowMenuHandle)
  else rowMenus.delete(id)
}
function imageMenu(img: WorkspaceImage): RowMenuItem[] {
  return [
    {
      key: 'check',
      label: checkingId.value === img.id ? 'Checking…' : 'Check for update',
      icon: SearchCheck,
      disabled: stateOf(img.id) !== 'present' || checkingId.value === img.id,
      title: stateOf(img.id) !== 'present' ? 'Download the image first' : undefined,
      action: () => checkUpdate(img),
    },
    {
      key: 'toggle',
      label: img.enabled ? 'Disable' : 'Enable',
      icon: img.enabled ? ToggleRight : ToggleLeft,
      disabled: togglingId.value === img.id,
      action: () => toggleEnabled(img),
    },
    { key: 'edit', label: 'Edit', icon: Pencil, action: () => openEdit(img) },
    { key: 'delete', label: 'Delete…', icon: Trash2, danger: true, separator: true, action: () => confirmDelete(img) },
  ]
}

function pullIcon(id: number) {
  const s = stateOf(id)
  if (s === 'present') return CheckCircle2
  if (s === 'pulling') return Loader
  return CircleDashed
}
function pullLabel(id: number): string {
  const s = stateOf(id)
  const d = details.value[id]
  if (s === 'present') return d?.size ? `Downloaded · ${fmtBytes(d.size)}` : 'Downloaded'
  if (s === 'pulling') {
    const p = d?.pull_progress
    return p && p.total ? `Pulling ${fmtBytes(p.current)} / ${fmtBytes(p.total)}` : 'Pulling…'
  }
  if (s === 'absent') return 'Not downloaded'
  return '—'
}
function usedByTitle(id: number): string {
  const n = details.value[id]?.workspace_count
  if (n === undefined) return ''
  return n ? `${n} workspace${n === 1 ? '' : 's'} (all users) use this image` : 'No workspace uses this image'
}
function updateLabel(id: number): string {
  const u = updates.value[id]
  if (!u) return ''
  if (u.up_to_date === true) return 'up to date'
  if (u.up_to_date === false) return 'update available'
  return 'could not check'
}
function updateClass(id: number) {
  const u = updates.value[id]?.up_to_date
  return u === false ? 'warn' : u === true ? 'ok' : ''
}
async function checkUpdate(img: WorkspaceImage) {
  checkingId.value = img.id
  try {
    // Await first: spreading before the await would drop a check that
    // finished meanwhile for another image.
    const result = await imagesApi.checkUpdate(img.id, zoneId.value)
    updates.value = { ...updates.value, [img.id]: result }
  } catch (e: any) {
    ui.toast(e.message || 'Update check failed', 'error')
  } finally {
    checkingId.value = null
  }
}

async function handlePull(img: WorkspaceImage) {
  try {
    await imagesApi.pull(img.id, zoneId.value)
    pending.value = { ...pending.value, [img.id]: 'pulling' }
    const { [img.id]: _drop, ...rest } = updates.value
    updates.value = rest
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

function openAdd() {
  editingId.value = null
  formError.value = ''
  Object.assign(form, EMPTY_FORM)
  showForm.value = true
}

function openEdit(img: WorkspaceImage) {
  editingId.value = img.id
  formError.value = ''
  Object.assign(form, {
    name: img.name,
    docker_image: img.docker_image,
    image_type: img.image_type,
    description: img.description ?? '',
    internal_port: img.internal_port ?? 3000,
    logo_url: img.logo_url ?? '',
  })
  showForm.value = true
}

async function handleSubmit() {
  if (editingId.value !== null) return handleEdit()
  return handleAdd()
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
    Object.assign(form, EMPTY_FORM)
  } catch (e: any) { formError.value = e.message }
  finally { adding.value = false }
}

async function handleEdit() {
  formError.value = ''
  adding.value = true
  try {
    const updated = await imagesApi.update(editingId.value!, {
      name: form.name,
      docker_image: form.docker_image,
      image_type: form.image_type as ImageType,
      description: form.description || undefined,
      internal_port: form.internal_port,
      logo_url: form.logo_url || undefined,
    })
    const idx = images.value.findIndex(i => i.id === editingId.value)
    if (idx !== -1) images.value[idx] = updated
    showForm.value = false
    await loadPullStatus()
    ui.toast('Image updated', 'success')
  } catch (e: any) { formError.value = e.message }
  finally { adding.value = false }
}

async function handleReset() {
  resetting.value = true
  try {
    const res = await imagesApi.sync(true)
    await load()
    await loadPullStatus()
    showReset.value = false
    ui.toast(`Catalog reset — ${res.added} added, ${res.updated} updated (${res.total} total)`, 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { resetting.value = false }
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
    pending.value = { ...pending.value, [id]: 'absent' }
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
.status-dot { font-family: var(--font-mono); font-size: 12px; letter-spacing: 1px; }
.enabled { color: var(--green); }
.disabled { color: var(--text-muted); }
.pull-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.pull-state.present { color: var(--green); }
.name-stack { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.ref { align-self: flex-start; font-size: 11px; padding: 0 4px; }
.sub { display: block; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.sub.warn { color: var(--amber); }
.sub.ok { color: var(--green); }
.row-disabled td:not(.actions) { opacity: 0.55; }
.pull-state.pulling { color: var(--accent); }
.pull-state.absent, .pull-state.unknown { color: var(--text-muted); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.form { display: flex; flex-direction: column; gap: 14px; }
.form-actions { display: flex; gap: 8px; justify-content: flex-end; }

/* Reset-catalog confirmation. */
.reset-modal { display: flex; flex-direction: column; gap: 14px; }
.reset-q { margin: 0; font-size: 14px; color: var(--text); }
.reset-desc { margin: 0; font-size: 13px; color: var(--text-muted); line-height: 1.6; }
.reset-desc strong { color: var(--accent); }

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
.opt-desc { font-size: 12px; color: var(--text-muted); line-height: 1.5; }
.opt-desc code { font-size: 11px; }
.delete-actions { display: flex; justify-content: flex-end; }
</style>
