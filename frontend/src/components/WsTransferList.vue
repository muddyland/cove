<template>
  <div v-if="mine.length" class="transfers" role="status" aria-live="polite">
    <div class="t-head">
      <span>{{ summary }}</span>
      <button v-if="mine.some(t => t.status === 'done')" type="button" class="link" @click="store.clearDone(wsId, source)">Clear finished</button>
    </div>
    <ul>
      <li v-for="t in mine" :key="t.id" :class="t.status">
        <component :is="ICON[t.status]" :size="13" class="t-ico" :class="{ spin: t.status === 'uploading' }" />
        <span class="t-name" :title="t.name">{{ t.savedAs && t.savedAs !== t.name ? `${t.name} → ${t.savedAs}` : t.name }}</span>
        <span class="t-where">~/{{ t.dir || '' }}</span>
        <span v-if="t.status === 'uploading' || t.status === 'queued'" class="t-bar">
          <span class="t-fill" :style="{ width: pct(t) + '%' }" />
        </span>
        <span v-else-if="t.status === 'error'" class="t-err" :title="t.error">{{ t.error }}</span>
        <span v-else class="t-size">{{ fmtBytes(t.total) }}</span>
        <button v-if="t.status === 'error' || t.status === 'done'" type="button" class="x" title="Dismiss" @click="store.dismiss(t.id)"><X :size="12" /></button>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, CheckCircle2, Clock, Loader2, X } from 'lucide-vue-next'
import { useWsTransfersStore, type WsTransfer } from '@/stores/wsTransfers'
import { fmtBytes } from '@/utils/workspaceStatus'

const props = defineProps<{ wsId: number; source?: WsTransfer['source'] }>()
const store = useWsTransfersStore()
const ICON = { queued: Clock, uploading: Loader2, done: CheckCircle2, error: AlertTriangle }

const mine = computed(() => store.items.filter(t => t.wsId === props.wsId && (!props.source || t.source === props.source)))
const summary = computed(() => {
  const left = mine.value.filter(t => t.status === 'queued' || t.status === 'uploading').length
  const failed = mine.value.filter(t => t.status === 'error').length
  if (left) return `Uploading ${left} file${left === 1 ? '' : 's'}…`
  return failed ? `${failed} upload${failed === 1 ? '' : 's'} failed` : 'Uploads finished'
})
function pct(t: WsTransfer) {
  return t.total ? Math.round((t.loaded / t.total) * 100) : 0
}
</script>

<style scoped>
.transfers { border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 10px; background: var(--surface); }
.t-head { display: flex; justify-content: space-between; align-items: center; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px; }
.link { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 11px; text-transform: none; letter-spacing: 0; padding: 0; }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; max-height: 150px; overflow: auto; }
li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto 110px auto; align-items: center; gap: 8px; font-size: 12px; }
.t-ico { color: var(--text-muted); }
.done .t-ico { color: var(--green); }
.error .t-ico { color: var(--red); }
.uploading .t-ico { color: var(--accent); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.t-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
.t-where { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); white-space: nowrap; }
.t-bar { height: 5px; border-radius: 3px; background: rgba(255, 255, 255, 0.07); overflow: hidden; }
.t-fill { display: block; height: 100%; background: var(--accent); transition: width 0.2s; }
.t-size { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); text-align: right; }
.t-err { color: var(--red); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.x { background: none; border: none; color: var(--text-muted); cursor: pointer; padding: 0 2px; display: inline-flex; }
.x:hover { color: var(--text); }
li:not(:has(.x))::after { content: ''; }
</style>
