<template>
  <BaseModal v-model="open" title="Open Website">
    <form @submit.prevent="handleSubmit" class="form">
      <div class="form-group">
        <label>// website url</label>
        <input v-model="url" type="url" placeholder="https://app.example.com" required autofocus />
      </div>
      <div class="form-group">
        <label>// browser</label>
        <select v-model="browserId" required>
          <option v-if="!browsers.length" value="" disabled>No browser images — sync the catalog first</option>
          <option v-for="b in browsers" :key="b.id" :value="b.id">{{ b.name }}</option>
        </select>
      </div>
      <label class="checkbox-row">
        <input type="checkbox" v-model="useTailscale" />
        <span>Route through Tailscale</span>
      </label>
      <div v-if="error" class="form-error">⚠ {{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading" :disabled="!browsers.length">Launch</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import { imagesApi } from '@/api/images'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type { WorkspaceImage } from '@/types'

const open = defineModel<boolean>({ default: false })

const images = ref<WorkspaceImage[]>([])
const url = ref('')
const browserId = ref<number | ''>('')
const useTailscale = ref(false)
const loading = ref(false)
const error = ref('')
const store = useWorkspacesStore()
const ui = useUiStore()
const router = useRouter()

const browsers = computed(() => images.value.filter(i => i.image_type === 'browser' || i.url_env))

onMounted(async () => {
  images.value = await imagesApi.list()
  if (browsers.value.length) browserId.value = browsers.value[0].id
})

function deriveName(u: string): string {
  try {
    return new URL(u).hostname.replace(/^www\./, '')
  } catch {
    return 'website'
  }
}

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    const ws = await store.launch({
      name: deriveName(url.value),
      image_id: browserId.value as number,
      workspace_type: 'browser',
      target_url: url.value,
      use_tailscale: useTailscale.value,
    })
    open.value = false
    ui.toast(`Opening ${deriveName(url.value)}…`, 'info')
    url.value = ''
    useTailscale.value = false
    router.push(`/workspace/${ws.id}`)
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
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
}
.checkbox-row input { width: auto; margin: 0; }
</style>
