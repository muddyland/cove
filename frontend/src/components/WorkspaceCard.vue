<template>
  <div class="card" :class="{ interactive: ws.status === 'running' }" @click="open">
    <div class="card-header">
      <div class="card-title">{{ ws.name }}</div>
      <div class="card-header-right" @click.stop>
        <button class="edit-btn" type="button" title="Edit workspace" @click="showEdit = true">
          <Pencil :size="14" />
        </button>
        <StatusBadge :status="ws.status" />
      </div>
    </div>
    <div class="card-meta">
      <span class="image-name">
        <img
          v-if="ws.image_logo"
          :src="ws.image_logo"
          class="image-logo"
          alt=""
          @error="hideLogo"
        />
        {{ ws.image_name }}
      </span>
      <span v-if="ws.target_url" class="target-url" :title="ws.target_url"><Globe :size="12" /> {{ truncateUrl(ws.target_url) }}</span>
      <span
        v-if="ws.use_tailscale"
        class="ts-badge"
        :title="ws.ts_exit_node ? `Routed through Tailscale · exit node: ${ws.ts_exit_node}` : 'Routed through Tailscale'"
      ><Network :size="12" /> Tailscale<template v-if="ws.ts_exit_node"> · {{ ws.ts_exit_node }}</template></span>
    </div>
    <div v-if="ws.error_message" class="error-msg">{{ ws.error_message }}</div>
    <div class="card-actions" @click.stop>
      <NeonButton v-if="ws.status === 'running'" variant="primary" @click="open"><Play :size="14" /> CONNECT</NeonButton>
      <NeonButton v-if="ws.status === 'stopped' || ws.status === 'error'" variant="secondary" :loading="acting" @click="handleStart"><Power :size="14" /> BOOT</NeonButton>
      <NeonButton v-if="ws.status === 'running'" variant="secondary" :loading="acting" @click="handleStop"><Square :size="14" /> HALT</NeonButton>
      <NeonButton variant="ghost" :loading="removing" @click="openPurge"><Trash2 :size="14" /> PURGE</NeonButton>
    </div>
  </div>
  <ConfirmModal
    v-model="showConfirm"
    title="Purge Workspace"
    :message="`Terminate '${ws.name}'? The container is destroyed.`"
    confirm-label="PURGE"
    :loading="removing"
    @confirm="handleRemove"
  >
    <label class="purge-storage">
      <input type="checkbox" v-model="purgeStorage" />
      <span>
        Also delete persistent storage
        <small>Permanently erases this workspace's home directory. Cannot be undone.</small>
      </span>
    </label>
  </ConfirmModal>
  <EditWorkspaceModal v-model="showEdit" :ws="ws" />
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import StatusBadge from './StatusBadge.vue'
import NeonButton from './NeonButton.vue'
import ConfirmModal from './ConfirmModal.vue'
import EditWorkspaceModal from './EditWorkspaceModal.vue'
import { Globe, Network, Play, Power, Square, Trash2, Pencil } from 'lucide-vue-next'
import type { Workspace } from '@/types'

const props = defineProps<{ ws: Workspace }>()
const store = useWorkspacesStore()
const ui = useUiStore()
const router = useRouter()

const acting = ref(false)
const removing = ref(false)
const showConfirm = ref(false)
const purgeStorage = ref(false)
const showEdit = ref(false)

function hideLogo(e: Event) {
  ;(e.target as HTMLImageElement).style.display = 'none'
}

function truncateUrl(url: string) {
  try {
    const u = new URL(url)
    return u.hostname + (u.pathname !== '/' ? u.pathname.slice(0, 20) : '')
  } catch {
    return url.slice(0, 30)
  }
}

function open() {
  if (props.ws.status === 'running') router.push(`/workspace/${props.ws.id}`)
}

async function handleStop() {
  acting.value = true
  try { await store.stop(props.ws.id) }
  catch (e: any) { ui.toast(e.message, 'error') }
  finally { acting.value = false }
}

async function handleStart() {
  acting.value = true
  try { await store.start(props.ws.id) }
  catch (e: any) { ui.toast(e.message, 'error') }
  finally { acting.value = false }
}

function openPurge() {
  purgeStorage.value = false
  showConfirm.value = true
}

async function handleRemove() {
  removing.value = true
  try {
    await store.remove(props.ws.id, purgeStorage.value)
    showConfirm.value = false
    ui.toast(purgeStorage.value ? 'Workspace purged (storage deleted)' : 'Workspace purged', 'success')
    purgeStorage.value = false
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { removing.value = false }
}
</script>

<style scoped>
.purge-storage {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin: -8px 0 20px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text);
}
/* Reset the global `input { width: 100% }` so the checkbox doesn't stretch
   across the row (which squeezed the label into a sliver + overflowed the modal). */
.purge-storage input { width: auto; margin: 0; padding: 0; flex-shrink: 0; margin-top: 3px; }
.purge-storage span { min-width: 0; }
.purge-storage small { display: block; color: var(--text-muted); font-size: 11px; margin-top: 2px; }

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  transition: border-color 0.2s, box-shadow 0.2s;
  position: relative;
  overflow: hidden;
}
/* Corner accent */
.card::before {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 32px; height: 2px;
  background: var(--border);
  transition: background 0.2s, box-shadow 0.2s;
}
.card.interactive { cursor: pointer; }
.card.interactive:hover {
  border-color: var(--accent);
  box-shadow: 0 0 16px rgba(0, 245, 255, 0.1);
}
.card.interactive:hover::before {
  background: var(--accent);
  box-shadow: var(--glow-sm);
}

.card-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.card-title {
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0.5px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-header-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.edit-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s, box-shadow 0.2s;
}
.edit-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
  box-shadow: var(--glow-sm);
}

.card-meta { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.image-name { font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); display: inline-flex; align-items: center; gap: 6px; }
.image-logo { width: 20px; height: 20px; border-radius: var(--radius-sm); object-fit: cover; flex-shrink: 0; }
.target-url { font-size: 11px; color: var(--accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex; align-items: center; gap: 4px; }
.target-url svg { flex-shrink: 0; }
.ts-badge { font-size: 11px; color: var(--accent-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-mono); }
.ts-badge svg { flex-shrink: 0; }

.error-msg {
  font-size: 11px;
  color: var(--red);
  background: rgba(255, 32, 85, 0.06);
  border: 1px solid rgba(255, 32, 85, 0.2);
  border-radius: var(--radius-sm);
  padding: 6px 8px;
  font-family: var(--font-mono);
}

/* Pin the action row to the bottom so cards of varying body length keep
   their buttons aligned across the row. */
.card-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: auto; }
</style>
