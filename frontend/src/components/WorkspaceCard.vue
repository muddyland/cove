<template>
  <div class="card" :class="{ interactive: ws.status === 'running' }" @click="open">
    <div class="card-header">
      <div class="card-title">{{ ws.name }}</div>
      <StatusBadge :status="ws.status" />
    </div>
    <div class="card-meta">
      <span class="image-name">{{ ws.image_name }}</span>
      <span v-if="ws.target_url" class="target-url" :title="ws.target_url">⬡ {{ truncateUrl(ws.target_url) }}</span>
    </div>
    <div v-if="ws.error_message" class="error-msg">{{ ws.error_message }}</div>
    <div class="card-actions" @click.stop>
      <NeonButton v-if="ws.status === 'running'" variant="primary" @click="open">CONNECT</NeonButton>
      <NeonButton v-if="ws.status === 'stopped' || ws.status === 'error'" variant="secondary" :loading="acting" @click="handleStart">BOOT</NeonButton>
      <NeonButton v-if="ws.status === 'running'" variant="secondary" :loading="acting" @click="handleStop">HALT</NeonButton>
      <NeonButton variant="ghost" :loading="removing" @click="showConfirm = true">PURGE</NeonButton>
    </div>
  </div>
  <ConfirmModal
    v-model="showConfirm"
    title="Purge Workspace"
    :message="`Terminate '${ws.name}'? Container destroyed. Storage preserved.`"
    confirm-label="PURGE"
    :loading="removing"
    @confirm="handleRemove"
  />
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import StatusBadge from './StatusBadge.vue'
import NeonButton from './NeonButton.vue'
import ConfirmModal from './ConfirmModal.vue'
import type { Workspace } from '@/types'

const props = defineProps<{ ws: Workspace }>()
const store = useWorkspacesStore()
const ui = useUiStore()
const router = useRouter()

const acting = ref(false)
const removing = ref(false)
const showConfirm = ref(false)

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

async function handleRemove() {
  removing.value = true
  try {
    await store.remove(props.ws.id)
    showConfirm.value = false
    ui.toast('Workspace purged', 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { removing.value = false }
}
</script>

<style scoped>
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
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

.card-header { display: flex; align-items: center; justify-content: space-between; }
.card-title {
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.card-meta { display: flex; flex-direction: column; gap: 3px; }
.image-name { font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); }
.target-url { font-size: 11px; color: var(--accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.error-msg {
  font-size: 11px;
  color: var(--red);
  background: rgba(255, 32, 85, 0.06);
  border: 1px solid rgba(255, 32, 85, 0.2);
  border-radius: var(--radius-sm);
  padding: 6px 8px;
  font-family: var(--font-mono);
}

.card-actions { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
