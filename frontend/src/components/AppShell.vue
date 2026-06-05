<template>
  <div class="shell">
    <header class="topnav">
      <RouterLink to="/" class="logo">
        <img class="logo-icon" src="/favicon.svg" alt="" />
        <span class="logo-text">COVE</span>
      </RouterLink>

      <nav class="nav-links">
        <RouterLink to="/" class="nav-link" :class="{ active: $route.path === '/' }">
          Dashboard
        </RouterLink>
        <template v-if="auth.isAdmin">
          <RouterLink to="/admin/sessions" class="nav-link" :class="{ active: $route.path === '/admin/sessions' }">
            Sessions
          </RouterLink>
          <RouterLink to="/admin/users" class="nav-link" :class="{ active: $route.path === '/admin/users' }">
            Users
          </RouterLink>
          <RouterLink to="/admin/images" class="nav-link" :class="{ active: $route.path === '/admin/images' }">
            Images
          </RouterLink>
          <RouterLink to="/admin/audit" class="nav-link" :class="{ active: $route.path === '/admin/audit' }">
            Audit
          </RouterLink>
        </template>
      </nav>

      <div class="nav-right">
        <button
          class="crt-btn"
          :class="{ active: ui.crt }"
          :title="ui.crt ? 'CRT effect on' : 'CRT effect off'"
          @click="ui.toggleCrt()"
        >▦ CRT</button>
        <div class="user-chip">
          <span class="user-dot" />
          <span class="username">{{ auth.user?.username }}</span>
          <span v-if="auth.user?.is_admin" class="admin-tag">ADMIN</span>
        </div>
        <button class="logout-btn" @click="handleLogout">[ EXIT ]</button>
      </div>
    </header>

    <main class="content">
      <slot />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { useRouter, RouterLink } from 'vue-router'

const auth = useAuthStore()
const ui = useUiStore()
const router = useRouter()

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.shell { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

.topnav {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 0 24px;
  height: 52px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  position: relative;
}
/* Bottom neon line */
.topnav::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0.6;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--accent);
  text-decoration: none;
  margin-right: 32px;
  flex-shrink: 0;
}
.logo-icon {
  width: 34px;
  height: 34px;
  display: block;
}
.logo-text {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 4px;
  color: var(--accent);
  text-shadow: var(--glow-sm);
}

.nav-links {
  display: flex;
  align-items: stretch;
  gap: 0;
  height: 100%;
  flex: 1;
}

.nav-link {
  display: flex;
  align-items: center;
  padding: 0 16px;
  color: var(--text-muted);
  text-decoration: none;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  font-family: var(--font-mono);
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
  position: relative;
}
.nav-link:hover {
  color: var(--text);
  background: var(--accent-dim);
}
.nav-link.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  text-shadow: var(--glow-sm);
}

.nav-right {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 12px;
}
.user-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
  animation: blink 2s infinite;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

.username { font-family: var(--font-mono); color: var(--text); }
.admin-tag {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1px;
  color: var(--accent-2);
  border: 1px solid var(--accent-2);
  border-radius: var(--radius-sm);
  padding: 1px 4px;
  text-shadow: 0 0 6px var(--accent-2);
}

.logout-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 11px;
  font-family: var(--font-mono);
  cursor: pointer;
  letter-spacing: 1px;
  padding: 4px;
  transition: color 0.15s;
}
.logout-btn:hover { color: var(--red); text-shadow: 0 0 8px var(--red); }

.crt-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 10px;
  font-family: var(--font-mono);
  letter-spacing: 1px;
  padding: 4px 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.crt-btn:hover { color: var(--text); border-color: var(--text-muted); }
.crt-btn.active {
  color: var(--accent);
  border-color: var(--accent);
  text-shadow: var(--glow-sm);
}

.content { flex: 1; overflow-y: auto; padding: 28px; }
</style>
