<template>
  <AppShell>
    <div class="page-header">
      <h2>// AUDIT LOG</h2>
      <div class="header-actions">
        <NeonButton variant="secondary" :disabled="!entries.length" @click="exportCsv"><FileDown :size="14" /> CSV</NeonButton>
        <NeonButton variant="secondary" :loading="loading" @click="reload"><RefreshCw :size="14" /> Refresh</NeonButton>
      </div>
    </div>

    <form class="filters" @submit.prevent="reload">
      <div class="form-group">
        <label for="f-user">User</label>
        <select id="f-user" v-model="filters.user" @change="reload">
          <option value="">Anyone</option>
          <option v-for="u in facets.users" :key="u" :value="u">{{ u }}</option>
        </select>
      </div>
      <div class="form-group">
        <label for="f-action">Action</label>
        <select id="f-action" v-model="filters.action" @change="reload">
          <option value="">Any action</option>
          <optgroup v-for="g in actionGroups" :key="g.prefix" :label="g.prefix">
            <option :value="`${g.prefix}.`">All {{ g.prefix }}.*</option>
            <option v-for="a in g.actions" :key="a" :value="a">{{ a }}</option>
          </optgroup>
        </select>
      </div>
      <div class="form-group">
        <label for="f-since">From</label>
        <input id="f-since" v-model="filters.since" type="date" @change="reload" />
      </div>
      <div class="form-group">
        <label for="f-until">To</label>
        <input id="f-until" v-model="filters.until" type="date" @change="reload" />
      </div>
      <div class="form-group grow">
        <label for="f-q">Detail contains</label>
        <input id="f-q" v-model="filters.q" type="search" placeholder="workspace id, username, path…" @keyup.enter="reload" />
      </div>
      <NeonButton v-if="hasFilters" type="button" variant="ghost" class="clear" @click="clearFilters"><X :size="13" /> Clear</NeonButton>
    </form>

    <LoadingSpinner v-if="loading && !entries.length" block />
    <div v-else-if="!entries.length" class="empty">{{ hasFilters ? 'No entries match these filters.' : 'No audit entries.' }}</div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>User</th>
            <th>Action</th>
            <th>Target / detail</th>
            <th>IP</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in entries" :key="entry.id">
            <td class="nowrap" :title="relativeTime(entry.ts)">{{ formatDate(entry.ts) }}</td>
            <td>
              <button v-if="entry.username" type="button" class="link" title="Show only this user" @click="filterBy('user', entry.username)">{{ entry.username }}</button>
              <span v-else>—</span>
            </td>
            <td><button type="button" class="link code" title="Show only this action" @click="filterBy('action', entry.action)"><code>{{ entry.action }}</code></button></td>
            <td class="detail" :title="entry.detail || ''">
              <!-- Workspace entries: the name instead of a 32-char public id. -->
              <template v-if="entry.workspace_name">
                <span class="target"><Monitor :size="12" /> {{ entry.workspace_name }}</span>
                <span v-if="detailExtra(entry)" class="extra">{{ detailExtra(entry) }}</span>
              </template>
              <template v-else-if="isDeletedWorkspace(entry)">
                <span class="target gone" title="This workspace has since been deleted"><Monitor :size="12" /> deleted workspace</span>
                <span class="extra">{{ entry.detail?.slice(0, 8) }}…</span>
              </template>
              <template v-else>{{ entry.detail || '—' }}</template>
            </td>
            <td>{{ entry.ip || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <div class="pager">
        <span class="count">{{ entries.length }} entr{{ entries.length === 1 ? 'y' : 'ies' }} shown</span>
        <NeonButton v-if="hasMore" variant="secondary" :loading="loadingMore" @click="loadMore">Load {{ PAGE }} more</NeonButton>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { absoluteTime, relativeTime } from '@/utils/workspaceStatus'
import { ref, reactive, computed, onMounted } from 'vue'
import AppShell from '@/components/AppShell.vue'
import NeonButton from '@/components/NeonButton.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import { RefreshCw, FileDown, X, Monitor } from 'lucide-vue-next'
import { adminApi } from '@/api/admin'
import { useUiStore } from '@/stores/ui'
import type { AuditEntry, AuditFacets, AuditQuery } from '@/types'

const PAGE = 100
const entries = ref<AuditEntry[]>([])
const facets = ref<AuditFacets>({ users: [], actions: [] })
const loading = ref(false)
const loadingMore = ref(false)
const hasMore = ref(false)
const ui = useUiStore()
const filters = reactive({ user: '', action: '', since: '', until: '', q: '' })

const hasFilters = computed(() => Object.values(filters).some(Boolean))
// Group actions by their prefix (workspace., admin.user., …) for the dropdown.
const actionGroups = computed(() => {
  const groups = new Map<string, string[]>()
  for (const a of facets.value.actions) {
    const prefix = a.includes('.') ? a.slice(0, a.lastIndexOf('.')) : a
    if (!groups.has(prefix)) groups.set(prefix, [])
    groups.get(prefix)!.push(a)
  }
  return [...groups.entries()].map(([prefix, actions]) => ({ prefix, actions }))
})

function query(offset: number): AuditQuery {
  // Date inputs are local calendar days; send the whole day as UTC instants.
  const since = filters.since ? new Date(`${filters.since}T00:00:00`).toISOString() : undefined
  const until = filters.until ? new Date(`${filters.until}T23:59:59.999`).toISOString() : undefined
  return { user: filters.user, action: filters.action, q: filters.q.trim(), since, until, limit: PAGE, offset }
}

async function reload() {
  loading.value = true
  try {
    const rows = await adminApi.audit.list(query(0))
    entries.value = rows
    hasMore.value = rows.length === PAGE
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}
async function loadMore() {
  loadingMore.value = true
  try {
    const rows = await adminApi.audit.list(query(entries.value.length))
    entries.value = [...entries.value, ...rows]
    hasMore.value = rows.length === PAGE
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    loadingMore.value = false
  }
}
function filterBy(field: 'user' | 'action', value: string) {
  filters[field] = value
  reload()
}
function clearFilters() {
  Object.assign(filters, { user: '', action: '', since: '', until: '', q: '' })
  reload()
}

onMounted(async () => {
  reload()
  try {
    facets.value = await adminApi.audit.facets()
  } catch {
    // Filters still work as free text without the dropdown values.
  }
})

const PID_RE = /^[0-9a-f]{32}(?=[\s:]|$)/
function detailExtra(e: AuditEntry): string {
  return (e.detail ?? '').replace(PID_RE, '').replace(/^[\s:]+/, '')
}
function isDeletedWorkspace(e: AuditEntry): boolean {
  return !e.workspace_name && /^(workspace|admin\.session)\./.test(e.action) && PID_RE.test(e.detail ?? '')
}

// API timestamps are naive UTC; parse them as UTC, not local time.
const formatDate = absoluteTime

function exportCsv() {
  const cols = ['time_utc', 'user', 'action', 'workspace', 'detail', 'ip']
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = [cols.join(',')]
  for (const e of entries.value) {
    lines.push([e.ts, e.username, e.action, e.workspace_name, e.detail, e.ip].map(esc).join(','))
  }
  const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `cove-audit-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
</script>

<style scoped>
@import '@/styles/tables.css';
.header-actions { display: flex; gap: 8px; }
.filters {
  display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end;
  margin-bottom: 20px; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface);
}
.filters .form-group { min-width: 150px; flex: 0 1 180px; }
.filters .form-group.grow { flex: 1 1 220px; }
.filters label { margin-bottom: 0; }
.clear { margin-bottom: 2px; }
.nowrap { white-space: nowrap; }
.detail { max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.target { display: inline-flex; align-items: center; gap: 5px; color: var(--text); }
.target.gone { color: var(--text-muted); font-style: italic; }
.extra { margin-left: 8px; color: var(--text-muted); font-family: var(--font-mono); font-size: 12px; }
.link {
  background: none; border: none; padding: 0; cursor: pointer;
  color: inherit; font: inherit; text-align: left;
}
.link:hover { color: var(--accent); text-decoration: underline; }
.link.code:hover { text-decoration: none; }
.link.code:hover code { box-shadow: 0 0 0 1px var(--accent); }
.pager { display: flex; align-items: center; justify-content: space-between; padding: 14px 4px; }
.count { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }
</style>
