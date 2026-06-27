import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { zonesApi } from '@/api/zones'
import type { ZoneOption } from '@/types'

// Enrolled zones available to the current user, for the launch/migrate pickers
// and the per-workspace zone badge. Cached after the first fetch.
export const useZonesStore = defineStore('zones', () => {
  const items = ref<ZoneOption[]>([])
  const loaded = ref(false)

  async function fetch(force = false) {
    if (loaded.value && !force) return items.value
    try {
      items.value = await zonesApi.userList()
      loaded.value = true
    } catch {
      // Non-fatal: pickers just won't show beyond the local zone.
    }
    return items.value
  }

  // True once there's somewhere other than the local zone to launch/migrate to.
  const hasRemote = computed(() => items.value.length > 1)
  const nameFor = (id: number) => items.value.find(z => z.id === id)?.name ?? null

  return { items, loaded, fetch, hasRemote, nameFor }
})
