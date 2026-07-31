<template>
  <AppShell>
    <div class="page-header">
      <h2>// FILE STORAGE</h2>
      <div class="header-actions">
        <select v-if="zones.hasRemote" v-model.number="zoneId" class="zone-select" @change="onZoneChange">
          <option v-for="z in zones.items" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <input ref="fileInput" type="file" multiple class="hidden-input" @change="handleFileUpload" />
        <input ref="folderInput" type="file" webkitdirectory multiple class="hidden-input" @change="handleFolderUpload" />
        <template v-if="view === 'files'">
          <NeonButton v-if="clipboard" variant="ghost" @click="paste">
            <ClipboardPaste :size="14" /> Paste{{ clipboard.mode === 'cut' ? ' (move)' : '' }}
          </NeonButton>
          <NeonButton variant="ghost" :disabled="uploading" @click="triggerFolderUpload"><FolderUp :size="14" /> Folder</NeonButton>
          <NeonButton variant="primary" :loading="uploading" @click="triggerUpload"><Upload :size="14" /> {{ uploadLabel }}</NeonButton>
        </template>
      </div>
    </div>

    <div class="tabs">
      <button class="tab" :class="{ active: view === 'files' }" @click="switchView('files')"><Folder :size="13" /> Files</button>
      <button class="tab" :class="{ active: view === 'trash' }" @click="switchView('trash')">
        <Trash2 :size="13" /> Trash<span v-if="trashEntries.length" class="badge">{{ trashEntries.length }}</span>
      </button>
    </div>

    <!-- ── FILES ──────────────────────────────────────────────────────────── -->
    <template v-if="view === 'files'">
      <nav class="breadcrumb">
        <button
          class="crumb crumb-home"
          :class="{ 'drop-target': dropDir === '' }"
          @click="navigate('')"
          @dragover.prevent="onDirDragOver('', $event)"
          @dragleave="onDirDragLeave('')"
          @drop.prevent.stop="onDirDrop('')"
        ><House :size="13" /> root</button>
        <template v-for="(seg, i) in segments" :key="i">
          <span class="sep">/</span>
          <button class="crumb" @click="navigate(segments.slice(0, i + 1).join('/'))">{{ seg }}</button>
        </template>
      </nav>

      <div
        class="table-wrap"
        :class="{ 'os-drag': osDragActive }"
        @dragover.prevent="onRootDragOver"
        @dragleave="onRootDragLeave"
        @drop.prevent="onRootDrop"
      >
        <div v-if="osDragActive" class="drop-overlay"><Upload :size="20" /> Drop files or folders to upload</div>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Size</th>
              <th>Modified</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="entry in entries"
              :key="entry.name"
              draggable="true"
              :class="{ 'drop-target': dropDir === join(entry.name) && entry.type === 'dir' }"
              @dragstart="onRowDragStart(entry, $event)"
              @dragend="onRowDragEnd"
              @dragover="entry.type === 'dir' ? onDirDragOver(join(entry.name), $event) : undefined"
              @dragleave="entry.type === 'dir' ? onDirDragLeave(join(entry.name)) : undefined"
              @drop="entry.type === 'dir' ? onDirDrop(join(entry.name), $event) : undefined"
            >
              <td>
                <button v-if="entry.type === 'dir'" class="name-btn dir" @click="navigate(join(entry.name))">
                  <Folder class="icon" :size="15" /> {{ entry.name }}
                </button>
                <span v-else class="name-btn"><File class="icon file-icon" :size="15" /> {{ entry.name }}</span>
              </td>
              <td>{{ entry.type === 'dir' ? '—' : humanSize(entry.size) }}</td>
              <td>{{ formatDate(entry.modified) }}</td>
              <td class="actions">
                <button class="icon-btn" title="Download" @click="downloadEntry(entry)"><Download :size="14" /></button>
                <button class="icon-btn" title="Copy" @click="copyEntry(entry)"><Copy :size="14" /></button>
                <button class="icon-btn" title="Cut" @click="cutEntry(entry)"><Scissors :size="14" /></button>
                <button class="icon-btn" title="Move to trash" @click="trashEntry(entry)"><Trash2 :size="14" /></button>
                <button class="icon-btn danger" title="Delete permanently" @click="confirmDeletePermanent(entry)"><X :size="14" /></button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!loading && !entries.length" class="empty">EMPTY DIRECTORY</div>
        <div v-if="loading" class="empty">LOADING…</div>
      </div>
    </template>

    <!-- ── TRASH ──────────────────────────────────────────────────────────── -->
    <template v-else>
      <div class="trash-bar">
        <span class="trash-note">Deleted files are recoverable until they expire, then auto-purged.</span>
        <NeonButton v-if="trashEntries.length" variant="danger" @click="confirmEmptyTrash"><Trash2 :size="13" /> Empty trash</NeonButton>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Original location</th>
              <th>Size</th>
              <th>Deleted</th>
              <th>Expires</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in trashEntries" :key="t.id">
              <td>
                <span class="name-btn">
                  <component :is="t.is_dir ? Folder : File" class="icon" :class="t.is_dir ? '' : 'file-icon'" :size="15" /> {{ t.name }}
                </span>
              </td>
              <td class="muted">{{ t.original_path }}</td>
              <td>{{ t.is_dir ? '—' : humanSize(t.size) }}</td>
              <td>{{ formatDate(t.deleted_at) }}</td>
              <td>{{ expiryLabel(t.expires_at) }}</td>
              <td class="actions">
                <NeonButton variant="ghost" @click="restore(t)"><RotateCcw :size="13" /> Restore</NeonButton>
                <NeonButton variant="danger" @click="confirmPurge(t)"><X :size="13" /> Delete</NeonButton>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!trashLoading && !trashEntries.length" class="empty">TRASH IS EMPTY</div>
        <div v-if="trashLoading" class="empty">LOADING…</div>
      </div>
    </template>

    <ConfirmModal
      v-model="showConfirm"
      :title="confirmDef.title"
      :message="confirmDef.message"
      :confirm-label="confirmDef.label"
      :loading="confirmBusy"
      @confirm="runConfirm"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { Upload, FolderUp, House, Folder, File, Download, Copy, Scissors, ClipboardPaste, Trash2, RotateCcw, X } from 'lucide-vue-next'
import { filesApi, type UploadItem } from '@/api/files'
import { useUiStore } from '@/stores/ui'
import { useZonesStore } from '@/stores/zones'
import type { FileEntry, TrashEntry } from '@/types'

const ui = useUiStore()
const zones = useZonesStore()

// Which zone's storage we're browsing. 0 = local control plane; the selector
// (shown only when remote zones exist) switches the whole view to that zone.
const zoneId = ref(0)
const path = ref('')
const entries = ref<FileEntry[]>([])
const loading = ref(false)

const view = ref<'files' | 'trash'>('files')
const trashEntries = ref<TrashEntry[]>([])
const trashLoading = ref(false)

const fileInput = ref<HTMLInputElement | null>(null)
const folderInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const uploadPct = ref(0)       // 0-100 for the current file
const uploadIndex = ref(0)     // 1-based index of the file being sent
const uploadTotal = ref(0)     // total files in this batch

// Copy/cut clipboard: paste targets the current directory.
const clipboard = ref<{ path: string; name: string; mode: 'copy' | 'cut' } | null>(null)

// Drag state. draggingPath is the row being dragged within the explorer (a move
// source); dropDir is the folder currently hovered; osDragActive is an OS file
// drag over the listing (an upload).
const draggingPath = ref<string | null>(null)
const dropDir = ref<string | null>(null)
const osDragActive = ref(false)

const segments = computed(() => path.value.split('/').filter(Boolean))

const uploadLabel = computed(() => {
  if (!uploading.value) return 'Upload'
  const batch = uploadTotal.value > 1 ? ` (${uploadIndex.value}/${uploadTotal.value})` : ''
  return `${uploadPct.value}%${batch}`
})

function join(name: string) {
  return path.value ? `${path.value}/${name}` : name
}

async function load() {
  loading.value = true
  try {
    const listing = await filesApi.list(path.value, zoneId.value)
    path.value = listing.path
    entries.value = listing.entries
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function loadTrash() {
  trashLoading.value = true
  try {
    trashEntries.value = await filesApi.listTrash(zoneId.value)
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    trashLoading.value = false
  }
}

function navigate(p: string) {
  path.value = p
  load()
}

function switchView(v: 'files' | 'trash') {
  view.value = v
  if (v === 'trash') loadTrash()
  else load()
}

// Switching zones resets to that zone's root — paths don't carry across nodes.
function onZoneChange() {
  path.value = ''
  clipboard.value = null
  load()
  loadTrash()
}

onMounted(async () => {
  await zones.fetch()
  await load()
  await loadTrash()  // populates the Trash tab badge
})

function formatDate(d: string) {
  return new Date(d).toLocaleString()
}

function expiryLabel(d: string): string {
  const ms = new Date(d).getTime() - Date.now()
  if (ms <= 0) return 'expiring…'
  const days = Math.floor(ms / 86400000)
  if (days >= 365 * 50) return 'never'
  if (days >= 1) return `in ${days}d`
  const hours = Math.max(1, Math.floor(ms / 3600000))
  return `in ${hours}h`
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let val = bytes / 1024
  let i = 0
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024
    i++
  }
  return `${val.toFixed(1)} ${units[i]}`
}

// ── Download ──
async function downloadEntry(entry: FileEntry) {
  try {
    if (entry.type === 'dir') await filesApi.downloadArchive(join(entry.name), zoneId.value)
    else await filesApi.download(join(entry.name), zoneId.value)
  } catch (e: any) {
    ui.toast(e.message, 'error')
  }
}

// ── Upload ──
function triggerUpload() {
  fileInput.value?.click()
}
function triggerFolderUpload() {
  folderInput.value?.click()
}

async function handleFileUpload(e: Event) {
  const input = e.target as HTMLInputElement
  const items: UploadItem[] = Array.from(input.files ?? []).map((file) => ({ file, relativePath: '' }))
  await uploadItems(items)
  input.value = ''
}

async function handleFolderUpload(e: Event) {
  const input = e.target as HTMLInputElement
  // webkitdirectory files carry a webkitRelativePath ("folder/sub/file.txt").
  const items: UploadItem[] = Array.from(input.files ?? []).map((file) => ({
    file,
    relativePath: (file as any).webkitRelativePath || file.name,
  }))
  await uploadItems(items)
  input.value = ''
}

// Upload a batch, recreating any sub-folders (from relativePath) under the
// current directory. Progress reuses the batch counter.
async function uploadItems(items: UploadItem[]) {
  if (!items.length) return
  uploading.value = true
  uploadTotal.value = items.length
  uploadIndex.value = 0
  let done = 0
  try {
    for (let i = 0; i < items.length; i++) {
      uploadIndex.value = i + 1
      uploadPct.value = 0
      const rel = items[i].relativePath
      const subdir = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : ''
      const dir = [path.value, subdir].filter(Boolean).join('/')
      await filesApi.upload(dir, items[i].file, zoneId.value, (frac) => {
        uploadPct.value = Math.round(frac * 100)
      })
      done++
    }
    ui.toast(items.length === 1 ? `Uploaded ${items[0].file.name}` : `Uploaded ${done} items`, 'success')
    await load()
  } catch (err: any) {
    ui.toast(err.message, 'error')
    if (done > 0) await load()
  } finally {
    uploading.value = false
    uploadPct.value = 0
    uploadIndex.value = 0
    uploadTotal.value = 0
  }
}

// ── Copy / cut / paste ──
function copyEntry(entry: FileEntry) {
  clipboard.value = { path: join(entry.name), name: entry.name, mode: 'copy' }
  ui.toast(`Copied ${entry.name}`, 'success')
}
function cutEntry(entry: FileEntry) {
  clipboard.value = { path: join(entry.name), name: entry.name, mode: 'cut' }
  ui.toast(`Cut ${entry.name}`, 'success')
}
async function paste() {
  const cb = clipboard.value
  if (!cb) return
  try {
    if (cb.mode === 'copy') await filesApi.copy(cb.path, path.value, zoneId.value)
    else await filesApi.move(cb.path, path.value, zoneId.value)
    if (cb.mode === 'cut') clipboard.value = null
    ui.toast(cb.mode === 'copy' ? 'Copied' : 'Moved', 'success')
    await load()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  }
}

// ── Trash (soft delete) ──
async function trashEntry(entry: FileEntry) {
  try {
    await filesApi.trash(join(entry.name), zoneId.value)
    ui.toast(`Moved ${entry.name} to trash`, 'success')
    if (clipboard.value?.path === join(entry.name)) clipboard.value = null
    await load()
    await loadTrash()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  }
}

async function restore(t: TrashEntry) {
  try {
    await filesApi.restore(t.id)
    ui.toast(`Restored ${t.name}`, 'success')
    await loadTrash()
    if (view.value === 'files') await load()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  }
}

// ── Drag & drop within the explorer (move) + OS→browser (upload) ──
function onRowDragStart(entry: FileEntry, e: DragEvent) {
  draggingPath.value = join(entry.name)
  e.dataTransfer?.setData('text/plain', join(entry.name))
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
function onRowDragEnd() {
  draggingPath.value = null
  dropDir.value = null
}
function onDirDragOver(dir: string, e: DragEvent) {
  if (!draggingPath.value) return  // OS file drag → handled by the root zone
  e.preventDefault()
  dropDir.value = dir
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
}
function onDirDragLeave(dir: string) {
  if (dropDir.value === dir) dropDir.value = null
}
async function onDirDrop(dir: string, e?: DragEvent) {
  e?.preventDefault()
  const src = draggingPath.value
  dropDir.value = null
  draggingPath.value = null
  if (!src) return
  // Ignore no-op drops (onto self or own current folder).
  const srcParent = src.includes('/') ? src.slice(0, src.lastIndexOf('/')) : ''
  if (src === dir || srcParent === dir) return
  try {
    await filesApi.move(src, dir, zoneId.value)
    if (clipboard.value?.path === src) clipboard.value = null
    await load()
  } catch (err: any) {
    ui.toast(err.message, 'error')
  }
}

function hasOsFiles(e: DragEvent): boolean {
  return !draggingPath.value && !!e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')
}
function onRootDragOver(e: DragEvent) {
  if (hasOsFiles(e)) osDragActive.value = true
}
function onRootDragLeave(e: DragEvent) {
  // Only clear when leaving the drop zone entirely (not moving between children).
  if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) osDragActive.value = false
}
async function onRootDrop(e: DragEvent) {
  osDragActive.value = false
  if (!hasOsFiles(e) || !e.dataTransfer) return
  const items = await itemsFromDataTransfer(e.dataTransfer)
  await uploadItems(items)
}

// Recursively read dropped files/folders into a flat UploadItem list. Uses the
// webkitGetAsEntry API (folder support) and falls back to loose files.
async function itemsFromDataTransfer(dt: DataTransfer): Promise<UploadItem[]> {
  const out: UploadItem[] = []
  const roots = Array.from(dt.items)
    .map((it) => (it as any).webkitGetAsEntry?.())
    .filter(Boolean)
  if (roots.length) {
    for (const entry of roots) await readEntry(entry, '', out)
  } else {
    for (const file of Array.from(dt.files)) out.push({ file, relativePath: '' })
  }
  return out
}
async function readEntry(entry: any, prefix: string, out: UploadItem[]): Promise<void> {
  if (entry.isFile) {
    const file: File = await new Promise((res, rej) => entry.file(res, rej))
    out.push({ file, relativePath: prefix + entry.name })
  } else if (entry.isDirectory) {
    const reader = entry.createReader()
    const readBatch = (): Promise<any[]> => new Promise((res, rej) => reader.readEntries(res, rej))
    let batch = await readBatch()
    while (batch.length) {
      for (const child of batch) await readEntry(child, `${prefix}${entry.name}/`, out)
      batch = await readBatch()
    }
  }
}

// ── Confirm modal (permanent delete / purge / empty trash) ──
const showConfirm = ref(false)
const confirmBusy = ref(false)
const confirmDef = reactive({ title: '', message: '', label: 'Delete' })
let confirmAction: (() => Promise<void>) | null = null

function openConfirm(def: { title: string; message: string; label: string }, action: () => Promise<void>) {
  confirmDef.title = def.title
  confirmDef.message = def.message
  confirmDef.label = def.label
  confirmAction = action
  showConfirm.value = true
}
async function runConfirm() {
  if (!confirmAction) return
  confirmBusy.value = true
  try {
    await confirmAction()
    showConfirm.value = false
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    confirmBusy.value = false
  }
}

function confirmDeletePermanent(entry: FileEntry) {
  openConfirm(
    {
      title: 'Delete permanently',
      message: entry.type === 'dir'
        ? `Permanently delete folder “${entry.name}” and everything inside it? This skips the trash and can't be undone.`
        : `Permanently delete “${entry.name}”? This skips the trash and can't be undone.`,
      label: 'Delete permanently',
    },
    async () => {
      await filesApi.remove(join(entry.name), zoneId.value)
      ui.toast('Deleted', 'success')
      await load()
    },
  )
}

function confirmPurge(t: TrashEntry) {
  openConfirm(
    { title: 'Delete permanently', message: `Permanently delete “${t.name}” from the trash? This can't be undone.`, label: 'Delete permanently' },
    async () => {
      await filesApi.purge(t.id)
      ui.toast('Deleted', 'success')
      await loadTrash()
    },
  )
}

function confirmEmptyTrash() {
  openConfirm(
    { title: 'Empty trash', message: `Permanently delete all ${trashEntries.value.length} item(s) in the trash? This can't be undone.`, label: 'Empty trash' },
    async () => {
      for (const t of [...trashEntries.value]) await filesApi.purge(t.id)
      ui.toast('Trash emptied', 'success')
      await loadTrash()
    },
  )
}
</script>

<style scoped>
@import '@/styles/tables.css';

.header-actions { display: flex; gap: 8px; align-items: center; }
.hidden-input { display: none; }
.zone-select {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text); font-family: var(--font-mono); font-size: 12px; padding: 6px 8px;
}

.tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }
.tab {
  display: inline-flex; align-items: center; gap: 6px;
  background: none; border: none; border-bottom: 2px solid transparent; cursor: pointer;
  color: var(--text-muted); font-family: var(--font-mono); font-size: 12px;
  padding: 8px 12px; transition: color 0.15s, border-color 0.15s;
}
.tab:hover { color: var(--text); }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.badge {
  background: var(--accent); color: var(--bg); border-radius: 8px;
  font-size: 10px; padding: 0 6px; min-width: 16px; text-align: center;
}

.breadcrumb {
  display: flex; align-items: center; flex-wrap: wrap; gap: 4px;
  margin-bottom: 16px;
  font-family: var(--font-mono); font-size: 12px;
}
.crumb {
  background: none; border: none; cursor: pointer;
  color: var(--accent); font-family: var(--font-mono); font-size: 12px;
  padding: 2px 4px; transition: text-shadow 0.15s;
}
.crumb:hover { text-shadow: var(--glow-sm); }
.crumb-home { display: inline-flex; align-items: center; gap: 4px; }
.crumb.drop-target { outline: 1px dashed var(--accent); border-radius: var(--radius-sm); }
.sep { color: var(--text-muted); }

.table-wrap { position: relative; }
.table-wrap.os-drag { outline: 2px dashed var(--accent); outline-offset: -4px; border-radius: var(--radius-sm); }
.drop-overlay {
  position: absolute; inset: 0; z-index: 5;
  display: flex; align-items: center; justify-content: center; gap: 10px;
  background: color-mix(in srgb, var(--bg) 80%, transparent);
  color: var(--accent); font-family: var(--font-mono); font-size: 13px;
  pointer-events: none;
}
tr.drop-target > td { background: color-mix(in srgb, var(--accent) 15%, transparent); }

.name-btn {
  background: none; border: none; cursor: default;
  color: var(--text); font-family: var(--font-mono); font-size: 13px;
  display: inline-flex; align-items: center; gap: 6px; padding: 0;
}
.name-btn.dir { cursor: pointer; color: var(--accent); }
.name-btn.dir:hover { text-shadow: var(--glow-sm); }
.icon { color: var(--accent-2); }
.file-icon { color: var(--text-muted); }
.muted { color: var(--text-muted); font-size: 12px; }

.actions { display: flex; gap: 4px; align-items: center; justify-content: flex-end; }
.icon-btn {
  background: none; border: 1px solid transparent; border-radius: var(--radius-sm);
  color: var(--text-muted); cursor: pointer; padding: 4px; display: inline-flex;
  transition: color 0.15s, border-color 0.15s;
}
.icon-btn:hover { color: var(--accent); border-color: var(--border); }
.icon-btn.danger:hover { color: var(--danger, #f56); border-color: var(--danger, #f56); }

.trash-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.trash-note { color: var(--text-muted); font-size: 12px; font-family: var(--font-mono); }
</style>
