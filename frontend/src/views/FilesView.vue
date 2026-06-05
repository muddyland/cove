<template>
  <AppShell>
    <div class="page-header">
      <h2>// FILE STORAGE</h2>
      <div class="header-actions">
        <input ref="fileInput" type="file" class="hidden-input" @change="handleUpload" />
        <NeonButton variant="primary" :loading="uploading" @click="triggerUpload"><Upload :size="14" /> Upload</NeonButton>
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
      :message="`Delete '${deleteTarget?.name}'?`"
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
import type { FileEntry } from '@/types'

const ui = useUiStore()

const path = ref('')
const entries = ref<FileEntry[]>([])
const loading = ref(false)

const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)

const showConfirm = ref(false)
const deleteTarget = ref<FileEntry | null>(null)
const deleting = ref(false)

const segments = computed(() => path.value.split('/').filter(Boolean))

function join(name: string) {
  return path.value ? `${path.value}/${name}` : name
}

async function load() {
  loading.value = true
  try {
    const listing = await filesApi.list(path.value)
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

onMounted(load)

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
    await filesApi.download(join(name))
  } catch (e: any) {
    ui.toast(e.message, 'error')
  }
}

function triggerUpload() {
  fileInput.value?.click()
}

async function handleUpload(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    await filesApi.upload(path.value, file)
    ui.toast(`Uploaded ${file.name}`, 'success')
    await load()
  } catch (err: any) {
    ui.toast(err.message, 'error')
  } finally {
    uploading.value = false
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
    await filesApi.remove(join(deleteTarget.value.name))
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

.header-actions { display: flex; gap: 8px; }
.hidden-input { display: none; }

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
