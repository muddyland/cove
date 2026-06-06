<template>
  <div class="proot-select">
    <input
      v-model="search"
      type="text"
      class="proot-search"
      placeholder="Search proot-apps…"
    />
    <div class="proot-list">
      <label v-for="app in filtered" :key="app" class="proot-item">
        <input type="checkbox" :checked="selectedSet.has(app)" @change="toggle(app)" />
        <span>{{ app }}</span>
      </label>
      <p v-if="!apps.length" class="proot-note">
        {{ loadError ? 'Catalog unavailable — proot-apps cannot be selected.' : 'Loading…' }}
      </p>
      <p v-else-if="!filtered.length" class="proot-note">No apps match “{{ search }}”.</p>
    </div>
    <p v-if="selected.length" class="proot-count">
      {{ selected.length }} selected: {{ selected.join(', ') }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { prootApi } from '@/api/proot'

// Selected app names. Bound with v-model from the parent form.
const selected = defineModel<string[]>({ default: () => [] })

const apps = ref<string[]>([])
const search = ref('')
const loadError = ref(false)

const selectedSet = computed(() => new Set(selected.value))
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? apps.value.filter(a => a.toLowerCase().includes(q)) : apps.value
})

function toggle(app: string) {
  selected.value = selectedSet.value.has(app)
    ? selected.value.filter(a => a !== app)
    : [...selected.value, app]
}

onMounted(async () => {
  try {
    apps.value = (await prootApi.list()).apps
  } catch {
    loadError.value = true
  }
})
</script>

<style scoped>
.proot-select { display: flex; flex-direction: column; gap: 8px; }
.proot-list {
  max-height: 180px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.proot-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 12px;
  color: var(--text);
}
.proot-item:hover { background: var(--surface-2); }
/* Reset the global `input { width: 100% }` so the checkbox doesn't stretch. */
.proot-item input { width: auto; margin: 0; padding: 0; flex-shrink: 0; }
.proot-note { font-size: 11px; color: var(--text-muted); margin: 4px 6px; }
.proot-count {
  font-size: 11px;
  color: var(--accent);
  margin: 0;
  word-break: break-word;
}
</style>
