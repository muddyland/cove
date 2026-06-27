<template>
  <BaseModal v-model="open" title="Migrate Workspace">
    <form @submit.prevent="handleSubmit" class="form">
      <p class="hint">
        Moves this workspace's home directory to another zone, then pins it there.
        The source copy is removed once the transfer succeeds. It stays stopped on
        the destination — boot it when you're ready.
      </p>

      <div class="form-group">
        <label>Destination zone</label>
        <select v-model.number="targetZone">
          <option v-for="z in targets" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
        <p v-if="!targets.length" class="hint">No other zones available to migrate to.</p>
      </div>

      <p class="hint">
        Currently on <strong>{{ ws.zone_name || 'Local' }}</strong>.
      </p>

      <div v-if="error" class="form-error">{{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading" :disabled="!targets.length">Migrate</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useZonesStore } from '@/stores/zones'
import { useUiStore } from '@/stores/ui'
import type { Workspace } from '@/types'

const props = defineProps<{ ws: Workspace }>()
const open = defineModel<boolean>({ default: false })

const store = useWorkspacesStore()
const zonesStore = useZonesStore()
const ui = useUiStore()

const loading = ref(false)
const error = ref('')
const targetZone = ref<number>(0)

// Every enrolled zone except the one the workspace is already on.
const targets = computed(() => zonesStore.items.filter(z => z.id !== props.ws.zone_id))

watch(open, async value => {
  if (!value) return
  error.value = ''
  await zonesStore.fetch()
  targetZone.value = targets.value[0]?.id ?? 0
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    await store.migrate(props.ws.id, { zone_id: targetZone.value })
    open.value = false
    const name = zonesStore.nameFor(targetZone.value) || 'zone'
    ui.toast(`Migrating ${props.ws.name} → ${name}…`, 'info')
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.form { display: flex; flex-direction: column; gap: 16px; }
.form-actions { display: flex; gap: 8px; justify-content: flex-end; }
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 0; }
</style>
