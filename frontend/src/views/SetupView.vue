<template>
  <div class="auth-page">
    <div class="grid-bg" />
    <div class="auth-card">
      <div class="auth-logo">
        <img class="logo-icon" src="/favicon.svg" alt="" />
        <span>COVE</span>
      </div>
      <div class="auth-subtitle">// FIRST BOOT — INITIALIZE NODE</div>
      <form @submit.prevent="handleSetup" class="form">
        <div class="form-group">
          <label>// admin identifier</label>
          <input v-model="username" required autocomplete="username" />
        </div>
        <div class="form-group">
          <label>// passkey (min 8 chars)</label>
          <input v-model="password" type="password" required minlength="8" autocomplete="new-password" />
        </div>
        <div v-if="error" class="form-error">⚠ {{ error }}</div>
        <NeonButton type="submit" variant="primary" :loading="loading" style="width:100%">
          INITIALIZE
        </NeonButton>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import NeonButton from '@/components/NeonButton.vue'

const auth = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleSetup() {
  error.value = ''
  loading.value = true
  try {
    await auth.setup(username.value, password.value)
    router.push('/app')
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: var(--bg); position: relative; overflow: hidden;
}
.grid-bg {
  position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(0, 245, 255, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 245, 255, 0.03) 1px, transparent 1px);
  background-size: 40px 40px;
  mask-image: radial-gradient(ellipse at center, black 40%, transparent 80%);
}
.auth-card {
  background: var(--surface); border: 1px solid var(--accent);
  border-radius: var(--radius); padding: 40px; width: 380px;
  display: flex; flex-direction: column; gap: 20px;
  box-shadow: var(--glow), var(--shadow); position: relative; z-index: 1;
}
.auth-card::before, .auth-card::after {
  content: ''; position: absolute; width: 12px; height: 12px;
  border-color: var(--accent); border-style: solid;
}
.auth-card::before { top: -1px; right: -1px; border-width: 2px 2px 0 0; border-radius: 0 var(--radius) 0 0; }
.auth-card::after  { bottom: -1px; left: -1px; border-width: 0 0 2px 2px; border-radius: 0 0 0 var(--radius); }
.auth-logo {
  display: flex; align-items: center; justify-content: center; gap: 12px;
  color: var(--accent); font-family: var(--font-display); font-size: 22px;
  font-weight: 700; letter-spacing: 6px; text-shadow: var(--glow);
}
.logo-icon { width: 44px; height: 44px; display: block; }
.auth-subtitle {
  text-align: center; font-family: var(--font-mono); font-size: 10px;
  letter-spacing: 2px; color: var(--text-muted);
}
.form { display: flex; flex-direction: column; gap: 14px; }
</style>
