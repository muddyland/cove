<template>
  <BaseModal v-model="open" title="Clone Workspace">
    <form @submit.prevent="handleSubmit" class="form">
      <p class="hint">
        Creates a copy with this workspace's full home directory — browser
        sessions, app settings, and files all carry over. Pick a different image
        to switch distros while keeping your data.
      </p>

      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="My Desktop copy" required />
      </div>

      <div class="form-group">
        <label>Image</label>
        <select v-model="form.image_id">
          <option v-for="img in images" :key="img.id" :value="img.id">
            {{ img.name }} ({{ img.image_type }})
          </option>
        </select>
        <p class="hint">Defaults to the same image. Change it to move your home to another distro.</p>
      </div>

      <div v-if="error" class="form-error">{{ error }}</div>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="loading">Clone</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import { imagesApi } from '@/api/images'
import { useWorkspacesStore } from '@/stores/workspaces'
import { useUiStore } from '@/stores/ui'
import { useRouter } from 'vue-router'
import type { Workspace, WorkspaceImage } from '@/types'

const props = defineProps<{ ws: Workspace }>()
const open = defineModel<boolean>({ default: false })

const images = ref<WorkspaceImage[]>([])
const loading = ref(false)
const error = ref('')
const store = useWorkspacesStore()
const ui = useUiStore()
const router = useRouter()

const form = reactive({ name: '', image_id: 0 as number })

watch(open, async value => {
  if (!value) return
  error.value = ''
  form.name = `${props.ws.name} copy`
  form.image_id = props.ws.image_id
  if (!images.value.length) {
    try { images.value = await imagesApi.list() } catch { /* selector just shows current */ }
  }
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    const clone = await store.clone(props.ws.id, {
      name: form.name,
      image_id: form.image_id,
    })
    open.value = false
    ui.toast(`Cloning ${props.ws.name} → ${form.name}…`, 'info')
    router.push(`/app/workspace/${clone.id}`)
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
