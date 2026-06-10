<template>
  <BaseModal v-model="open" title="Diagnostics" width="720px">
    <div class="diag">
      <div class="tabs" role="tablist">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          class="tab"
          :class="{ active: t.key === active }"
          role="tab"
          @click="select(t.key)"
        >
          <component :is="t.icon" :size="13" /> {{ t.label }}
        </button>
      </div>

      <div class="output-head">
        <span class="output-label">{{ activeTab?.hint }}</span>
        <NeonButton variant="ghost" :loading="loading" @click="refresh">
          <RefreshCw :size="13" /> Refresh
        </NeonButton>
      </div>

      <pre class="output" :class="{ muted: !hasContent }">{{ body }}</pre>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { reactive, ref, computed, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import { workspacesApi, type LogSource } from '@/api/workspaces'
import { Monitor, Network, ShieldCheck, Activity, RefreshCw } from 'lucide-vue-next'
import type { Workspace } from '@/types'

type TabKey = 'ts-status' | 'desktop' | 'gluetun' | 'tailscale'

const props = defineProps<{ ws: Workspace; initialTab?: TabKey }>()
const open = defineModel<boolean>({ default: false })

const tabs = computed(() => {
  const list: { key: TabKey; label: string; hint: string; icon: any }[] = []
  if (props.ws.use_tailscale)
    list.push({ key: 'ts-status', label: 'Tailscale', hint: 'tailscale status', icon: Activity })
  list.push({ key: 'desktop', label: 'Desktop', hint: 'desktop container log', icon: Monitor })
  if (props.ws.use_gluetun)
    list.push({ key: 'gluetun', label: 'VPN', hint: 'gluetun sidecar log', icon: ShieldCheck })
  if (props.ws.use_tailscale)
    list.push({ key: 'tailscale', label: 'TS log', hint: 'tailscale sidecar log', icon: Network })
  return list
})

const active = ref<TabKey>('desktop')
const activeTab = computed(() => tabs.value.find(t => t.key === active.value))
const loading = ref(false)
// Per-tab cache so switching back doesn't refetch; { text, error } per key.
const cache = reactive<Record<string, { text: string; error: string }>>({})

const current = computed(() => cache[active.value])
const hasContent = computed(() => !!current.value?.text)
const body = computed(() => {
  const c = current.value
  if (loading.value && !c) return 'Loading…'
  if (!c) return ''
  if (c.error) return c.error
  return c.text || '(no output)'
})

const LOG_SOURCE: Record<string, LogSource> = {
  desktop: 'desktop',
  gluetun: 'gluetun',
  tailscale: 'tailscale',
}

async function load(key: TabKey) {
  loading.value = true
  try {
    if (key === 'ts-status') {
      const r = await workspacesApi.tailscaleStatus(props.ws.id)
      cache[key] = {
        text: r.available ? r.output : '',
        error: r.available ? '' : 'Tailscale sidecar is not running.',
      }
    } else {
      const r = await workspacesApi.logs(props.ws.id, LOG_SOURCE[key])
      cache[key] = {
        text: r.available ? r.output : '',
        error: r.available ? '' : 'Container is not running.',
      }
    }
  } catch (e: any) {
    cache[key] = { text: '', error: e?.message || 'Failed to load.' }
  } finally {
    loading.value = false
  }
}

function select(key: TabKey) {
  active.value = key
  if (!cache[key]) load(key)
}

function refresh() {
  load(active.value)
}

// On open, pick the requested/first tab and (re)load it fresh.
watch(open, value => {
  if (!value) return
  for (const k of Object.keys(cache)) delete cache[k]
  const wanted = props.initialTab && tabs.value.some(t => t.key === props.initialTab)
    ? props.initialTab
    : tabs.value[0]?.key ?? 'desktop'
  active.value = wanted
  load(wanted)
})
</script>

<style scoped>
.diag { display: flex; flex-direction: column; gap: 12px; }
.tabs { display: flex; flex-wrap: wrap; gap: 4px; }
.tab {
  display: inline-flex; align-items: center; gap: 5px;
  background: none; border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text-muted); cursor: pointer;
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 0.5px;
  padding: 6px 10px; transition: all 0.15s;
}
.tab:hover { color: var(--text); border-color: var(--accent); }
.tab.active { color: var(--accent); border-color: var(--accent); text-shadow: var(--glow-sm); }
.output-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.output-label { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); letter-spacing: 0.5px; }
.output {
  margin: 0; padding: 12px;
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  font-family: var(--font-mono); font-size: 11px; line-height: 1.55; color: var(--text);
  white-space: pre-wrap; word-break: break-word;
  min-height: 200px; max-height: 52vh; overflow: auto;
}
.output.muted { color: var(--text-muted); }
</style>
