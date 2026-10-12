<template>
  <BaseModal v-model="open" :title="`${ws.name} · Files`" width="760px">
    <div
      class="fb"
      @dragenter.prevent="onDragEnter"
      @dragover.prevent="onDragOver"
      @dragleave="onDragLeave"
      @drop.prevent="onDrop"
    >
      <div class="toolbar">
        <nav class="crumbs" aria-label="Folder">
          <button type="button" class="crumb" @click="go('')"><House :size="13" /> Home</button>
          <template v-for="(c, i) in crumbs" :key="c.path">
            <ChevronRight :size="12" class="crumb-sep" />
            <button type="button" class="crumb" :class="{ last: i === crumbs.length - 1 }" @click="go(c.path)">{{ c.name }}</button>
          </template>
        </nav>
        <div class="tools">
          <button type="button" class="tool" :class="{ on: showHidden }" :title="showHidden ? 'Hide dotfiles' : 'Show dotfiles'" @click="showHidden = !showHidden">
            <component :is="showHidden ? Eye : EyeOff" :size="14" />
          </button>
          <button type="button" class="tool" title="Refresh" @click="load()"><RefreshCw :size="14" /></button>
          <button type="button" class="tool" title="New folder" @click="newFolder"><FolderPlus :size="14" /></button>
          <NeonButton variant="primary" @click="picker?.click()"><Upload :size="13" /> Upload</NeonButton>
          <input ref="picker" type="file" multiple hidden @change="onPick" />
        </div>
      </div>

      <div class="quick">
        <button v-for="q in QUICK" :key="q" type="button" class="chip" :class="{ on: path === q }" @click="go(q)">{{ q }}</button>
      </div>

      <div class="list" :class="{ dragging }">
        <LoadingSpinner v-if="loading && !entries.length" block />
        <p v-else-if="error" class="state err">⚠ {{ error }}</p>
        <p v-else-if="!visible.length" class="state">This folder is empty. Drop files here to add them.</p>
        <table v-else class="tbl">
          <tbody>
            <tr v-if="path" class="row up" @click="go(parent)">
              <td colspan="4"><CornerLeftUp :size="14" /> ..</td>
            </tr>
            <tr
              v-for="e in visible"
              :key="e.name"
              class="row"
              :class="{ dir: e.type === 'dir' }"
              @click="e.type === 'dir' ? go(join(e.name)) : undefined"
              @dblclick="e.type !== 'dir' ? download(e) : undefined"
            >
              <td class="name">
                <component :is="e.type === 'dir' ? Folder : e.type === 'link' ? Link2 : FileIcon" :size="14" class="ico" />
                <span>{{ e.name }}</span>
              </td>
              <td class="size">{{ e.type === 'dir' ? '' : fmtBytes(e.size) }}</td>
              <td class="when" :title="new Date(e.modified).toLocaleString()">{{ relativeTime(e.modified) }}</td>
              <td class="act">
                <button
                  v-if="e.type !== 'dir'"
                  type="button"
                  class="tool"
                  :title="`Download ${e.name}`"
                  @click.stop="download(e)"
                ><Download :size="14" /></button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="dragging" class="drop-hint" aria-hidden="true">
          <Upload :size="22" />
          <span>Drop to upload to <strong>{{ path ? `~/${path}` : 'the home folder' }}</strong></span>
        </div>
      </div>

      <TransferList :ws-id="ws.id" />

      <p class="hint">
        This is the workspace's home folder (<code>~</code>, i.e. <code>/config</code>), read and written as the
        workspace user. You can also drop files straight onto the stream: they land in
        <code>~/{{ dropFolder }}</code>.
        <template v-if="ws.ephemeral"> This workspace is ephemeral: anything here is gone when it stops.</template>
      </p>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import {
  ChevronRight, CornerLeftUp, Download, Eye, EyeOff, File as FileIcon, Folder, FolderPlus, House, Link2,
  RefreshCw, Upload,
} from 'lucide-vue-next'
import BaseModal from './BaseModal.vue'
import LoadingSpinner from './LoadingSpinner.vue'
import NeonButton from './NeonButton.vue'
import TransferList from './WsTransferList.vue'
import { workspaceFilesApi } from '@/api/workspaceFiles'
import { useWsTransfersStore } from '@/stores/wsTransfers'
import { useUiStore } from '@/stores/ui'
import { fmtBytes, relativeTime } from '@/utils/workspaceStatus'
import { dropFolderFor, filesFromDrop, isFileDrag } from '@/utils/wsDrop'
import type { Workspace, WsFileEntry } from '@/types'

const props = defineProps<{ ws: Workspace }>()
const open = defineModel<boolean>({ default: false })
const ui = useUiStore()
const transfers = useWsTransfersStore()

const QUICK = ['Desktop', 'Downloads', 'Documents']
const dropFolder = computed(() => dropFolderFor(props.ws))

const path = ref('')
const entries = ref<WsFileEntry[]>([])
const loading = ref(false)
const error = ref('')
const showHidden = ref(false)
const picker = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
let dragDepth = 0

const visible = computed(() => entries.value.filter(e => showHidden.value || !e.name.startsWith('.')))
const crumbs = computed(() => {
  const parts = path.value.split('/').filter(Boolean)
  return parts.map((name, i) => ({ name, path: parts.slice(0, i + 1).join('/') }))
})
const parent = computed(() => path.value.split('/').slice(0, -1).join('/'))

function join(name: string) {
  return path.value ? `${path.value}/${name}` : name
}

let loadSeq = 0
async function load() {
  const mine = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const res = await workspaceFilesApi.list(props.ws.id, path.value)
    if (mine !== loadSeq) return
    entries.value = res.entries
    path.value = res.path
  } catch (e: any) {
    if (mine !== loadSeq) return
    entries.value = []
    error.value = e?.message || 'Could not read this folder'
  } finally {
    if (mine === loadSeq) loading.value = false
  }
}

function go(p: string) {
  path.value = p
  entries.value = []
  load()
}

async function download(e: WsFileEntry) {
  try {
    await workspaceFilesApi.download(props.ws.id, join(e.name))
  } catch (err: any) {
    ui.toast(err?.message || 'Download failed', 'error')
  }
}

async function newFolder() {
  const name = window.prompt('New folder name')?.trim()
  if (!name) return
  if (name.includes('/')) { ui.toast('A folder name can\'t contain "/"', 'error'); return }
  try {
    await workspaceFilesApi.mkdir(props.ws.id, join(name))
    await load()
  } catch (e: any) {
    ui.toast(e?.message || 'Could not create the folder', 'error')
  }
}

function upload(list: FileList | File[] | null | undefined) {
  const arr = [...(list ?? [])]
  if (!arr.length) return
  transfers.enqueue(props.ws.id, path.value, arr)
}

function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  upload(input.files)
  input.value = ''
}

function onDragEnter(e: DragEvent) {
  if (!isFileDrag(e)) return
  dragDepth++
  dragging.value = true
}
function onDragOver(e: DragEvent) {
  if (isFileDrag(e) && e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
}
function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dragging.value = false
}
function onDrop(e: DragEvent) {
  dragDepth = 0
  dragging.value = false
  const { files, folders } = filesFromDrop(e.dataTransfer)
  if (folders) ui.toast(`Skipped ${folders} folder${folders === 1 ? '' : 's'} — drop the files inside instead`, 'info')
  upload(files)
}

// Refresh the listing as uploads into the shown folder finish.
const off = transfers.onFinished(t => {
  if (open.value && t.wsId === props.ws.id && t.dir === path.value && t.status === 'done') load()
})
onUnmounted(off)

watch(open, isOpen => {
  if (isOpen) {
    showHidden.value = false
    load()
  }
}, { immediate: true })
</script>

<style scoped>
.fb { display: flex; flex-direction: column; gap: 10px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.crumbs { display: flex; align-items: center; gap: 2px; flex-wrap: wrap; min-width: 0; }
.crumb {
  display: inline-flex; align-items: center; gap: 5px; background: none; border: none; cursor: pointer;
  color: var(--text-muted); font-family: var(--font-mono); font-size: 12px; padding: 3px 5px; border-radius: var(--radius-sm);
}
.crumb:hover { color: var(--accent); background: var(--accent-dim); }
.crumb.last { color: var(--text); }
.crumb-sep { color: var(--text-faint); }
.tools { display: flex; align-items: center; gap: 6px; }
.tool {
  display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 28px; padding: 0;
  background: none; border: 1px solid transparent; border-radius: var(--radius-sm); color: var(--text-muted); cursor: pointer;
}
.tool:hover, .tool.on { color: var(--accent); border-color: var(--border); background: var(--surface-2); }
.quick { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  font-family: var(--font-mono); font-size: 11px; padding: 2px 9px; border-radius: 999px; cursor: pointer;
  background: none; border: 1px solid var(--border); color: var(--text-muted);
}
.chip:hover, .chip.on { color: var(--accent); border-color: var(--accent); }
.list {
  position: relative; min-height: 240px; max-height: 46vh; overflow: auto;
  border: 1px solid var(--border); border-radius: var(--radius); transition: border-color 0.15s;
}
.list.dragging { border-color: var(--accent); border-style: dashed; }
.state { padding: 40px 16px; text-align: center; color: var(--text-muted); font-size: 13px; margin: 0; }
.state.err { color: var(--red); }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.row td { padding: 7px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.row:hover td { background: var(--accent-dim); }
.row.dir, .row.up { cursor: pointer; }
.row.up td { color: var(--text-muted); font-family: var(--font-mono); }
.name { width: 100%; max-width: 0; overflow: hidden; text-overflow: ellipsis; }
.name span { overflow: hidden; text-overflow: ellipsis; }
.ico { vertical-align: -2px; margin-right: 8px; color: var(--text-muted); }
.row.dir .ico { color: var(--accent-2); }
.row.dir .name span { color: var(--accent); }
.size, .when { color: var(--text-muted); font-family: var(--font-mono); font-size: 12px; text-align: right; }
.act { width: 1%; text-align: right; }
.drop-hint {
  position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
  background: color-mix(in srgb, var(--bg) 82%, transparent); color: var(--accent); font-size: 13px; pointer-events: none;
}
.hint { font-family: var(--font-mono); font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 0; }
.hint code { color: var(--accent); }
</style>
