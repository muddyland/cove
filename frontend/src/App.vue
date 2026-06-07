<template>
  <RouterView />
  <ToastHost />
  <UpdateBanner />
</template>

<script setup lang="ts">
import { RouterView } from 'vue-router'
import ToastHost from '@/components/ToastHost.vue'
import UpdateBanner from '@/components/UpdateBanner.vue'
</script>
