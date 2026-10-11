import { ref, onMounted, onUnmounted } from 'vue'

// One shared clock for relative times ("up 2 h", "auto-stops in 40 min") so a
// grid of cards re-renders on a single timer instead of one per card.
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null
let users = 0

export function useNow(intervalMs = 30_000) {
  onMounted(() => {
    users++
    now.value = Date.now()
    if (!timer) timer = setInterval(() => (now.value = Date.now()), intervalMs)
  })
  onUnmounted(() => {
    users = Math.max(0, users - 1)
    if (!users && timer) {
      clearInterval(timer)
      timer = null
    }
  })
  return now
}
