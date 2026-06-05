<template>
  <BaseModal v-model="open" title="Launch Workspace">
    <form @submit.prevent="handleSubmit" class="form">
      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="My Desktop" required />
      </div>
      <div class="form-group">
        <label>Image</label>
        <select v-model="form.image_id" required>
          <option value="" disabled>Select an image…</option>
          <option v-for="img in images" :key="img.id" :value="img.id">
            {{ img.name }} ({{ img.image_type }})
          </option>
        </select>
      </div>
      <div v-if="urlCapable" class="form-group">
        <label>{{ urlRequired ? 'Target URL' : 'Open URL (optional)' }}</label>
        <input
          v-model="form.target_url"
          type="url"
          placeholder="https://example.com"
          :required="urlRequired"
        />
      </div>
      <label class="checkbox-row">
        <input type="checkbox" v-model="form.use_tailscale" />
        <span>Route through Tailscale</span>
      </label>
      <div v-if="error" class="form-error">{{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading">Launch</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import { imagesApi } from '@/api/images'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type { WorkspaceImage } from '@/types'

const open = defineModel<boolean>({ default: false })

const images = ref<WorkspaceImage[]>([])
const loading = ref(false)
const error = ref('')
const store = useWorkspacesStore()
const ui = useUiStore()
const router = useRouter()

const form = reactive({ name: '', image_id: '' as number | '', target_url: '', use_tailscale: false })

const selectedImage = computed(() => images.value.find(i => i.id === form.image_id))
const urlCapable = computed(() =>
  !!selectedImage.value && (!!selectedImage.value.url_env || selectedImage.value.image_type === 'link'),
)
const urlRequired = computed(() => selectedImage.value?.image_type === 'link')

onMounted(async () => {
  images.value = await imagesApi.list()
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    const ws = await store.launch({
      name: form.name,
      image_id: form.image_id as number,
      workspace_type: selectedImage.value?.image_type ?? 'desktop',
      target_url: urlCapable.value && form.target_url ? form.target_url : undefined,
      use_tailscale: form.use_tailscale,
    })
    open.value = false
    ui.toast(`Launching ${form.name}…`, 'info')
    form.name = ''
    form.image_id = ''
    form.target_url = ''
    form.use_tailscale = false
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
