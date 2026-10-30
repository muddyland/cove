<template>
  <div class="codes-box">
    <p class="hint">
      Keep these somewhere safe, away from your phone and security key. Each one signs you in once
      if you lose them. Cove won't show them again.
    </p>
    <ol class="codes">
      <li v-for="c in codes" :key="c"><code>{{ c }}</code></li>
    </ol>
    <div class="row">
      <NeonButton type="button" variant="ghost" @click="copy"><Copy :size="13" /> {{ copied ? 'Copied' : 'Copy' }}</NeonButton>
      <NeonButton type="button" variant="ghost" @click="download"><Download :size="13" /> Download</NeonButton>
      <span class="spacer" />
      <NeonButton type="button" variant="primary" @click="$emit('done')">I've saved them</NeonButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Copy, Download } from 'lucide-vue-next'
import NeonButton from './NeonButton.vue'

const props = defineProps<{ codes: string[]; username?: string }>()
defineEmits<{ done: [] }>()
const copied = ref(false)

function text() {
  return `Cove recovery codes${props.username ? ` for ${props.username}` : ''} (${location.host})\n\n${props.codes.join('\n')}\n`
}

async function copy() {
  try {
    await navigator.clipboard.writeText(text())
    copied.value = true
  } catch {
    copied.value = false
  }
}

function download() {
  // A data: URL: Cove's CSP has no blob: sources.
  const a = document.createElement('a')
  a.href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(text())
  a.download = 'cove-recovery-codes.txt'
  a.click()
}
</script>

<style scoped>
.codes-box { display: flex; flex-direction: column; gap: 12px; }
.codes { columns: 2; gap: 24px; margin: 0; padding: 12px 16px 12px 36px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.codes li { font-size: 14px; padding: 2px 0; color: var(--text-muted); }
.codes code { font-family: var(--font-mono); color: var(--text); letter-spacing: 1px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.spacer { flex: 1; }
.hint { color: var(--text-muted); font-size: 13px; line-height: 1.5; margin: 0; }
</style>
