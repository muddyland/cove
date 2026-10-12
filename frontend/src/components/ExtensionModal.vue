<template>
  <BaseModal v-model="open" title="Browser extension" width="560px">
    <div class="ext">
      <p class="hint">
        <strong>Open in Cove</strong> adds a right-click action to your own browser:
        send any link to a fresh Cove browser workspace instead of opening it here.
        You pick the browser, the network route (direct, a VPN profile or a Tailscale
        profile) and the start-up options, and the page loads in a container.
      </p>
      <p class="hint">
        <strong>For Chrome-based browsers</strong> — Chrome, Edge, Brave, Vivaldi,
        Opera, Helium and other Chromium forks. There is no Firefox build.
      </p>

      <div class="ext-actions">
        <NeonButton variant="primary" :loading="downloading" @click="download">
          <Download :size="14" /> Download{{ info.version ? ` v${info.version}` : '' }}
        </NeonButton>
        <span class="hint">{{ size }} · unpacked extension (zip)</span>
      </div>

      <ol class="steps-list">
        <li>Unzip it somewhere it can stay — your browser loads the extension from that folder every time it starts.</li>
        <li>Open <code>chrome://extensions</code> (Edge <code>edge://extensions</code>, Brave <code>brave://extensions</code>…).</li>
        <li>Turn on <strong>Developer mode</strong>.</li>
        <li>Click <strong>Load unpacked</strong> and pick the unzipped folder — the one holding <code>manifest.json</code>.</li>
        <li>Open the extension's <strong>options</strong> and enter this Cove address: <code>{{ origin }}</code></li>
      </ol>
      <p class="hint">
        To update later, unzip the new version over the same folder and press
        <strong>Reload</strong> on the extension's card.
      </p>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Download } from 'lucide-vue-next'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import { extensionApi } from '@/api/extension'
import { useUiStore } from '@/stores/ui'
import type { ExtensionInfo } from '@/types'

const props = defineProps<{ info: ExtensionInfo }>()
const open = defineModel<boolean>({ default: false })
const ui = useUiStore()

const origin = window.location.origin
const downloading = ref(false)
const size = computed(() => {
  const kb = props.info.size_bytes / 1024
  return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.round(kb)} KB`
})

async function download() {
  downloading.value = true
  try {
    await extensionApi.download(props.info.filename)
  } catch (e: any) {
    ui.toast(e?.message || 'Download failed', 'error')
  } finally {
    downloading.value = false
  }
}
</script>

<style scoped>
.ext { display: flex; flex-direction: column; gap: 14px; }
.hint { font-family: var(--font-mono); font-size: 12px; line-height: 1.5; color: var(--text-muted); margin: 0; }
.hint strong { color: var(--text); }
.ext-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.steps-list {
  margin: 0; padding-left: 20px;
  display: flex; flex-direction: column; gap: 6px;
  font-family: var(--font-mono); font-size: 12px; line-height: 1.55; color: var(--text-muted);
}
.steps-list strong { color: var(--text); }
.steps-list code { color: var(--accent); }
</style>
