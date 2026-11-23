<template>
  <RouterView />
  <ToastHost />
  <UpdateBanner />
  <OfflineOverlay />
  <TooltipLayer />
</template>

<script setup lang="ts">
import { RouterView } from 'vue-router'
import ToastHost from '@/components/ToastHost.vue'
import UpdateBanner from '@/components/UpdateBanner.vue'
import OfflineOverlay from '@/components/OfflineOverlay.vue'
import TooltipLayer from '@/components/TooltipLayer.vue'
</script>
