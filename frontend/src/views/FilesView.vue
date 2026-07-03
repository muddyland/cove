<template>
  <AppShell>
    <div class="page-header">
      <h2>// FILE STORAGE</h2>
      <div class="header-actions">
        <select v-if="zones.hasRemote" v-model.number="zoneId" class="zone-select" @change="onZoneChange">
          <option v-for="z in zones.items" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <input ref="fileInput" type="file" multiple class="hidden-input" @change="handleUpload" />
        <NeonButton variant="primary" :loading="uploading" @click="triggerUpload"><Upload :size="14" /> {{ uploadLabel }}</NeonButton>
      </div>
    </div>

    <nav class="breadcrumb">
      <button class="crumb crumb-home" @click="navigate('')"><House :size="13" /> root</button>
      <template v-for="(seg, i) in segments" :key="i">
        <span class="sep">/</span>
        <button class="crumb" @click="navigate(segments.slice(0, i + 1).join('/'))">{{ seg }}</button>
      </template>
    </nav>

    <div class="table-wrap">
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
          <tr v-for="entry in entries" :key="entry.name">
            <td>
              <button v-if="entry.type === 'dir'" class="name-btn dir" @click="navigate(join(entry.name))">
                <Folder class="icon" :size="15" /> {{ entry.name }}
              </button>
              <span v-else class="name-btn"><File class="icon file-icon" :size="15" /> {{ entry.name }}</span>
            </td>
            <td>{{ entry.type === 'dir' ? '—' : humanSize(entry.size) }}</td>
            <td>{{ formatDate(entry.modified) }}</td>
            <td class="actions">
              <NeonButton v-if="entry.type === 'file'" variant="ghost" @click="download(entry.name)"><Download :size="13" /> Download</NeonButton>
              <NeonButton variant="danger" @click="confirmDelete(entry)"><Trash2 :size="13" /> Delete</NeonButton>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!loading && !entries.length" class="empty">EMPTY DIRECTORY</div>
      <div v-if="loading" class="empty">LOADING…</div>
    </div>

    <ConfirmModal
      v-model="showConfirm"
      title="Delete"
      :message="deleteMessage"
      confirm-label="Delete"
      :loading="deleting"
      @confirm="handleDelete"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { Upload, House, Folder, File, Download, Trash2 } from 'lucide-vue-next'
import { filesApi } from '@/api/files'
import { useUiStore } from '@/stores/ui'
import { useZonesStore } from '@/stores/zones'
import type { FileEntry } from '@/types'

const ui = useUiStore()
const zones = useZonesStore()

// Which zone's storage we're browsing. 0 = local control plane; the selector
// (shown only when remote zones exist) switches the whole view to that zone.
const zoneId = ref(0)
const path = ref('')
const entries = ref<FileEntry[]>([])
const loading = ref(false)

const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const uploadPct = ref(0)       // 0-100 for the current file
const uploadIndex = ref(0)     // 1-based index of the file being sent
const uploadTotal = ref(0)     // total files in this batch

const showConfirm = ref(false)
const deleteTarget = ref<FileEntry | null>(null)
const deleting = ref(false)

const segments = computed(() => path.value.split('/').filter(Boolean))

const uploadLabel = computed(() => {
  if (!uploading.value) return 'Upload'
  const batch = uploadTotal.value > 1 ? ` (${uploadIndex.value}/${uploadTotal.value})` : ''
  return `${uploadPct.value}%${batch}`
})

// Folder deletes are recursive — spell that out so a user doesn't wipe a tree
// thinking it's a single item.
const deleteMessage = computed(() => {
  const t = deleteTarget.value
  if (!t) return ''
  return t.type === 'dir'
    ? `Delete folder “${t.name}” and everything inside it? This permanently removes all of its contents and can't be undone.`
    : `Delete “${t.name}”? This can't be undone.`
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

function navigate(p: string) {
  path.value = p
  load()
}

// Switching zones resets to that zone's root — paths don't carry across nodes.
function onZoneChange() {
  path.value = ''
  load()
}

onMounted(async () => {
  await zones.fetch()
  await load()
})

function formatDate(d: string) {
  return new Date(d).toLocaleString()
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

async function download(name: string) {
  try {
    await filesApi.download(join(name), zoneId.value)
  } catch (e: any) {
    ui.toast(e.message, 'error')
  }
}

function triggerUpload() {
  fileInput.value?.click()
}

async function handleUpload(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (!files.length) return
  uploading.value = true
  uploadTotal.value = files.length
  let done = 0
  try {
    for (let i = 0; i < files.length; i++) {
      uploadIndex.value = i + 1
      uploadPct.value = 0
      await filesApi.upload(path.value, files[i], zoneId.value, (frac) => {
        uploadPct.value = Math.round(frac * 100)
      })
      done++
    }
    ui.toast(files.length === 1 ? `Uploaded ${files[0].name}` : `Uploaded ${done} files`, 'success')
    await load()
  } catch (err: any) {
    ui.toast(err.message, 'error')
    if (done > 0) await load()  // some files landed before the failure
  } finally {
    uploading.value = false
    uploadPct.value = 0
    uploadIndex.value = 0
    uploadTotal.value = 0
    input.value = ''
  }
}

function confirmDelete(entry: FileEntry) {
  deleteTarget.value = entry
  showConfirm.value = true
}

async function handleDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await filesApi.remove(join(deleteTarget.value.name), zoneId.value)
    showConfirm.value = false
    ui.toast('Deleted', 'success')
    await load()
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    deleting.value = false
  }
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
.sep { color: var(--text-muted); }

.name-btn {
  background: none; border: none; cursor: default;
  color: var(--text); font-family: var(--font-mono); font-size: 13px;
  display: inline-flex; align-items: center; gap: 6px; padding: 0;
}
.name-btn.dir { cursor: pointer; color: var(--accent); }
.name-btn.dir:hover { text-shadow: var(--glow-sm); }
.icon { color: var(--accent-2); }
.file-icon { color: var(--text-muted); }
</style>
