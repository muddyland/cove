<template>
  <div class="shell">
    <header class="topnav">
      <RouterLink to="/" class="logo">
        <img class="logo-icon" src="/favicon.svg" alt="" />
        <span class="logo-text">COVE</span>
      </RouterLink>

      <nav class="nav-links">
        <RouterLink to="/" class="nav-link" :class="{ active: $route.path === '/' }">
          <LayoutGrid class="nav-icon" :size="16" /> Dashboard
        </RouterLink>
        <RouterLink to="/files" class="nav-link" :class="{ active: $route.path === '/files' }">
          <FolderOpen class="nav-icon" :size="16" /> Files
        </RouterLink>
        <template v-if="auth.isAdmin">
          <RouterLink to="/admin/sessions" class="nav-link" :class="{ active: $route.path === '/admin/sessions' }">
            <MonitorPlay class="nav-icon" :size="16" /> Sessions
          </RouterLink>
          <RouterLink to="/admin/users" class="nav-link" :class="{ active: $route.path === '/admin/users' }">
            <Users class="nav-icon" :size="16" /> Users
          </RouterLink>
          <RouterLink to="/admin/images" class="nav-link" :class="{ active: $route.path === '/admin/images' }">
            <Boxes class="nav-icon" :size="16" /> Images
          </RouterLink>
          <RouterLink to="/admin/audit" class="nav-link" :class="{ active: $route.path === '/admin/audit' }">
            <ScrollText class="nav-icon" :size="16" /> Audit
          </RouterLink>
          <RouterLink to="/admin/settings" class="nav-link" :class="{ active: $route.path === '/admin/settings' }">
            <Settings class="nav-icon" :size="16" /> Settings
          </RouterLink>
        </template>
      </nav>

      <div class="nav-right">
        <button
          class="crt-btn"
          :class="{ active: ui.crt }"
          :title="ui.crt ? 'CRT effect on' : 'CRT effect off'"
          @click="ui.toggleCrt()"
        ><ScanLine class="nav-icon" :size="14" /> CRT</button>
        <RouterLink to="/preferences" class="user-chip" title="Preferences">
          <UserRound class="nav-icon" :size="14" />
          <span class="username">{{ auth.user?.username }}</span>
          <span v-if="auth.user?.is_admin" class="admin-tag">ADMIN</span>
        </RouterLink>
        <button class="logout-btn" @click="handleLogout"><LogOut class="nav-icon" :size="14" /> EXIT</button>
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
import {
  LayoutGrid, FolderOpen, MonitorPlay, Users, Boxes,
  ScrollText, Settings, ScanLine, UserRound, LogOut,
} from 'lucide-vue-next'

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
  gap: 7px;
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
  text-decoration: none;
  cursor: pointer;
  transition: border-color 0.15s;
}
.user-chip:hover { border-color: var(--accent); }
.user-chip.router-link-active { border-color: var(--accent); box-shadow: var(--glow-sm); }
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

/* Shared icon styling — inherit currentColor, no shrink. */
.nav-icon { flex-shrink: 0; }
.nav-link.active .nav-icon { filter: drop-shadow(var(--glow-sm)); }
.crt-btn, .logout-btn { display: inline-flex; align-items: center; gap: 5px; }
.crt-btn.active .nav-icon { filter: drop-shadow(var(--glow-sm)); }
</style>
