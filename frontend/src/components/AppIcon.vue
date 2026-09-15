<template>
  <span class="app-icon" :style="{ width: `${size}px`, height: `${size}px` }">
    <img
      v-if="src && !failed"
      :src="src"
      alt=""
      loading="lazy"
      referrerpolicy="no-referrer"
      @error="failed = true"
    />
    <Package v-else :size="Math.round(size * 0.7)" />
  </span>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Package } from 'lucide-vue-next'

// An app's icon, or a generic package glyph when it has none or it fails to load.
const props = withDefaults(defineProps<{ src?: string | null; size?: number }>(), { src: null, size: 22 })

const failed = ref(false)
watch(() => props.src, () => { failed.value = false })
</script>

<style scoped>
.app-icon {
  display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
  color: var(--text-muted);
}
.app-icon img { width: 100%; height: 100%; object-fit: contain; }
</style>
