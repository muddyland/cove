<template>
  <div class="card" :class="{ interactive: ws.status === 'running', 'menu-open': actionsOpen }" @click="open">
    <div class="card-header">
      <div class="card-title">{{ ws.name }}</div>
      <div class="card-header-right">
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
      <span v-if="ws.zone_id !== 0" class="zone-badge" :title="`Runs on zone: ${ws.zone_name || ''}`"><Server :size="12" /> {{ ws.zone_name || 'Zone' }}</span>
      <span
        v-if="ws.use_tailscale"
        class="ts-badge"
        :title="ws.ts_exit_node ? `Routed through Tailscale · exit node: ${ws.ts_exit_node}` : 'Routed through Tailscale'"
      ><Network :size="12" /> Tailscale<template v-if="ws.ts_exit_node"> · {{ ws.ts_exit_node }}</template></span>
      <span v-if="ws.use_gluetun" class="vpn-badge" title="Routed through Gluetun VPN">
        <ShieldCheck :size="12" /> VPN
      </span>
    </div>
    <div v-if="stats" class="stats">
      <div class="stat">
        <div class="stat-head"><span><Cpu :size="11" /> CPU</span><span class="stat-val">{{ stats.cpu_pct.toFixed(0) }}%</span></div>
        <div class="stat-bar"><div class="stat-fill" :style="{ width: barWidth(stats.cpu_pct) }"></div></div>
      </div>
      <div class="stat">
        <div class="stat-head"><span><MemoryStick :size="11" /> MEM</span><span class="stat-val">{{ fmtBytes(stats.mem_used) }}</span></div>
        <div class="stat-bar"><div class="stat-fill" :style="{ width: barWidth(stats.mem_pct) }"></div></div>
      </div>
      <button
        v-if="stats.tailscale_ip"
        type="button"
        class="ts-ip"
        :title="`Tailscale address — click to copy\n${stats.tailscale_ip}`"
        @click.stop="copyIp(stats.tailscale_ip)"
      >
        <span><Network :size="11" /> TS</span>
        <span class="ts-ip-val">{{ stats.tailscale_ip }} <Copy :size="10" /></span>
      </button>
    </div>

    <div v-if="ws.error_message" class="error-msg">{{ ws.error_message }}</div>
    <div v-if="vpnLocked" class="vpn-lock-msg"><Lock :size="11" /> VPN in use by another workspace</div>
    <div class="card-actions" @click.stop>
      <!-- Primary: Connect (running) or Boot (stopped/error). -->
      <NeonButton v-if="ws.status === 'running'" variant="primary" @click="open"><Play :size="14" /> CONNECT</NeonButton>
      <NeonButton
        v-else
        variant="success"
        :loading="acting"
        :disabled="vpnLocked"
        :title="vpnLocked ? 'Another VPN workspace is active — only one VPN connection at a time. Stop it first.' : ''"
        @click="handleStart"
      ><component :is="vpnLocked ? Lock : Power" :size="14" /> BOOT</NeonButton>

      <!-- Everything else lives in the Actions menu. -->
      <div ref="actionsDd" class="actions-dd" :class="{ open: actionsOpen }">
        <NeonButton variant="secondary" class="actions-trigger" @click="actionsOpen = !actionsOpen">
          ACTIONS <ChevronDown :size="12" class="chev" />
        </NeonButton>
        <div v-show="actionsOpen" class="actions-menu">
          <button type="button" class="action-item" @click="act(() => (showEdit = true))"><Pencil :size="14" /> Edit</button>
          <button v-if="ws.status === 'running'" type="button" class="action-item" @click="act(() => (showDiag = true))"><Activity :size="14" /> Logs</button>
          <button v-if="ws.status === 'running'" type="button" class="action-item" @click="act(handleStop)"><Square :size="14" /> Halt</button>
          <button v-if="isStopped" type="button" class="action-item" @click="act(() => (showClone = true))"><CopyPlus :size="14" /> Clone</button>
          <button v-if="isStopped && zonesStore.hasRemote" type="button" class="action-item" @click="act(() => (showMigrate = true))"><ArrowRightLeft :size="14" /> Migrate</button>
          <button type="button" class="action-item danger" @click="act(openPurge)"><Trash2 :size="14" /> Purge</button>
        </div>
      </div>
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
  <CloneModal v-model="showClone" :ws="ws" />
  <MigrateModal v-model="showMigrate" :ws="ws" />
  <DiagnosticsModal v-model="showDiag" :ws="ws" />
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useZonesStore } from '@/stores/zones'
import { useUiStore } from '@/stores/ui'
import StatusBadge from './StatusBadge.vue'
import NeonButton from './NeonButton.vue'
import ConfirmModal from './ConfirmModal.vue'
import EditWorkspaceModal from './EditWorkspaceModal.vue'
import CloneModal from './CloneModal.vue'
import MigrateModal from './MigrateModal.vue'
import DiagnosticsModal from './DiagnosticsModal.vue'
import { Globe, Network, Server, ArrowRightLeft, Play, Power, Square, Trash2, Pencil, Cpu, MemoryStick, Copy, CopyPlus, ShieldCheck, Lock, Activity, ChevronDown } from 'lucide-vue-next'
import type { Workspace, WorkspaceStats } from '@/types'

const props = defineProps<{ ws: Workspace; stats?: WorkspaceStats | null }>()

function fmtBytes(n: number): string {
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB'
  if (n >= 1024 ** 2) return Math.round(n / 1024 ** 2) + ' MB'
  if (n >= 1024) return Math.round(n / 1024) + ' KB'
  return n + ' B'
}

function barWidth(pct: number): string {
  return Math.min(Math.max(pct, 0), 100).toFixed(0) + '%'
}
const store = useWorkspacesStore()
const zonesStore = useZonesStore()
const ui = useUiStore()
const router = useRouter()

// A Gluetun config allows a single connection, so a stopped/errored VPN
// workspace can't boot while another VPN workspace is already active.
const vpnLocked = computed(() =>
  props.ws.use_gluetun &&
  (props.ws.status === 'stopped' || props.ws.status === 'error') &&
  store.items.some(
    w => w.id !== props.ws.id && w.use_gluetun && (w.status === 'running' || w.status === 'creating'),
  ),
)

const acting = ref(false)
const removing = ref(false)
const showConfirm = ref(false)
const purgeStorage = ref(false)
const showEdit = ref(false)
const showClone = ref(false)
const showMigrate = ref(false)
const showDiag = ref(false)

const isStopped = computed(() => props.ws.status === 'stopped' || props.ws.status === 'error')

// Actions dropdown: run the chosen action and close the menu.
const actionsOpen = ref(false)
const actionsDd = ref<HTMLElement | null>(null)
function act(fn: () => void) {
  actionsOpen.value = false
  fn()
}
function onDocClick(e: MouseEvent) {
  if (actionsOpen.value && actionsDd.value && !actionsDd.value.contains(e.target as Node)) {
    actionsOpen.value = false
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

function hideLogo(e: Event) {
  ;(e.target as HTMLImageElement).style.display = 'none'
}

async function copyIp(ip: string) {
  try {
    await navigator.clipboard.writeText(ip)
    ui.toast(`Copied ${ip}`, 'success')
  } catch {
    ui.toast('Copy failed', 'error')
  }
}

function truncateUrl(url: string) {
  const urls = url.trim().split(/\s+/).filter(Boolean)
  const first = urls[0] ?? url
  let label: string
  try {
    const u = new URL(first)
    label = u.hostname + (u.pathname !== '/' ? u.pathname.slice(0, 20) : '')
  } catch {
    label = first.slice(0, 30)
  }
  return urls.length > 1 ? `${label} +${urls.length - 1} more` : label
}

function open() {
  if (props.ws.status === 'running') router.push(`/app/workspace/${props.ws.id}`)
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
/* Let the Actions dropdown escape the card's clip (and sit above neighbours). */
.card.menu-open { overflow: visible; z-index: 10; }
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
.card-meta { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.image-name { font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); display: inline-flex; align-items: center; gap: 6px; }
.image-logo { width: 20px; height: 20px; border-radius: var(--radius-sm); object-fit: cover; flex-shrink: 0; }
.target-url { font-size: 11px; color: var(--accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex; align-items: center; gap: 4px; }
.target-url svg { flex-shrink: 0; }
.ts-badge { font-size: 11px; color: var(--accent-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-mono); }
.ts-badge svg { flex-shrink: 0; }
.zone-badge { font-size: 11px; color: var(--accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-mono); }
.zone-badge svg { flex-shrink: 0; }
.vpn-badge { font-size: 11px; color: var(--green); display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-mono); letter-spacing: 0.5px; }
.vpn-badge svg { flex-shrink: 0; }
.vpn-lock-msg {
  font-size: 11px; color: var(--amber); font-family: var(--font-mono);
  display: inline-flex; align-items: center; gap: 5px;
  background: rgba(255, 170, 0, 0.06);
  border: 1px solid rgba(255, 170, 0, 0.2);
  border-radius: var(--radius-sm); padding: 6px 8px;
}
.vpn-lock-msg svg { flex-shrink: 0; }

.stats { display: flex; flex-direction: column; gap: 8px; }
.stat { display: flex; flex-direction: column; gap: 4px; }
.stat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 1px;
  color: var(--text-muted);
}
.stat-head > span:first-child { display: inline-flex; align-items: center; gap: 5px; }
.stat-val { color: var(--accent); }
.stat-bar {
  height: 4px;
  border-radius: 2px;
  background: var(--surface-2);
  overflow: hidden;
}
.stat-fill {
  height: 100%;
  background: var(--accent);
  box-shadow: 0 0 6px rgba(0, 245, 255, 0.4);
  border-radius: 2px;
  transition: width 0.4s ease;
}

/* Tailscale address row — a copyable chip styled to match the stat heads. */
.ts-ip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 1px;
  color: var(--text-muted);
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}
.ts-ip:hover { border-color: var(--accent-2); color: var(--text); }
.ts-ip > span:first-child { display: inline-flex; align-items: center; gap: 5px; color: var(--accent-2); }
.ts-ip-val { display: inline-flex; align-items: center; gap: 5px; color: var(--accent-2); }
.ts-ip svg { flex-shrink: 0; }

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
   their buttons aligned across the row. Buttons share the width equally and
   stay on a single line so every card has an identical full-width footer. */
.card-actions { display: flex; gap: 6px; margin-top: auto; }
.card-actions :deep(.btn) {
  flex: 1 1 0;
  min-width: 0;
  justify-content: center;
  padding: 8px 8px;
  letter-spacing: 0.5px;
  white-space: nowrap;
}

/* Actions dropdown */
.actions-dd { position: relative; display: flex; flex: 1 1 0; min-width: 0; }
.actions-trigger { width: 100%; }
.actions-trigger .chev { transition: transform 0.15s; margin-left: 2px; }
.actions-dd.open .actions-trigger .chev { transform: rotate(180deg); }
.actions-menu {
  position: absolute;
  bottom: calc(100% + 6px);   /* open upward — cards sit in a grid */
  right: 0;
  min-width: 168px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--glow-sm), var(--shadow);
  padding: 5px;
  z-index: 50;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.action-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 12px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 1px;
  text-transform: uppercase;
  font-family: var(--font-mono);
  border-radius: var(--radius-sm);
  text-align: left;
  transition: all 0.15s;
}
.action-item:hover { color: var(--text); background: var(--accent-dim); }
.action-item svg { flex-shrink: 0; }
.action-item.danger { color: var(--red); }
.action-item.danger:hover { background: color-mix(in srgb, var(--red) 14%, transparent); }
</style>
