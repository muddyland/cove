<template>
  <div class="shell">
    <header class="topnav">
      <RouterLink to="/app" class="logo">
        <img class="logo-icon" src="/favicon.svg" alt="" />
        <span class="logo-text">COVE</span>
      </RouterLink>

      <button
        class="menu-toggle"
        :aria-expanded="mobileOpen"
        aria-label="Toggle navigation"
        @click="mobileOpen = !mobileOpen"
      >
        <component :is="mobileOpen ? X : Menu" :size="20" />
      </button>

      <div class="nav-drawer" :class="{ open: mobileOpen }">
        <nav class="nav-links">
          <RouterLink to="/app" class="nav-link" :class="{ active: $route.path === '/app' }">
            <LayoutGrid class="nav-icon" :size="16" /> Dashboard
          </RouterLink>
          <RouterLink to="/app/files" class="nav-link" :class="{ active: $route.path === '/app/files' }">
            <FolderOpen class="nav-icon" :size="16" /> Files
          </RouterLink>
          <div v-if="auth.isAdmin" ref="adminDropdown" class="admin-dropdown" :class="{ open: adminOpen }">
            <button
              type="button"
              class="nav-link admin-trigger"
              :class="{ active: isAdminRoute }"
              :aria-expanded="adminOpen"
              @click="adminOpen = !adminOpen"
            >
              <Shield class="nav-icon" :size="16" /> Admin
              <ChevronDown class="chevron" :size="14" />
            </button>
            <div v-show="adminOpen" class="admin-menu">
              <RouterLink v-for="item in adminItems" :key="item.to" :to="item.to" class="admin-item" :class="{ active: $route.path === item.to }">
                <component :is="item.icon" class="nav-icon" :size="15" /> {{ item.label }}
              </RouterLink>
            </div>
          </div>
        </nav>

        <div class="nav-right">
          <RouterLink to="/app/preferences" class="user-chip" title="Preferences">
            <UserRound class="nav-icon" :size="14" />
            <span class="username">{{ auth.user?.username }}</span>
            <span v-if="auth.user?.is_admin" class="admin-tag">ADMIN</span>
          </RouterLink>
          <button class="logout-btn" @click="handleLogout"><LogOut class="nav-icon" :size="14" /> EXIT</button>
        </div>
      </div>
    </header>

    <main class="content">
      <slot />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useRouter, useRoute, RouterLink } from 'vue-router'
import {
  LayoutGrid, FolderOpen, MonitorPlay, Users, Boxes, Network,
  ScrollText, Settings, Shield, ChevronDown, UserRound, LogOut, Menu, X,
} from 'lucide-vue-next'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const adminItems = [
  { to: '/app/admin/sessions', label: 'Sessions', icon: MonitorPlay },
  { to: '/app/admin/users', label: 'Users', icon: Users },
  { to: '/app/admin/images', label: 'Images', icon: Boxes },
  { to: '/app/admin/zones', label: 'Zones', icon: Network },
  { to: '/app/admin/audit', label: 'Audit', icon: ScrollText },
  { to: '/app/admin/settings', label: 'Settings', icon: Settings },
]

const mobileOpen = ref(false)
const adminOpen = ref(false)
const adminDropdown = ref<HTMLElement | null>(null)
const isAdminRoute = computed(() => route.path.startsWith('/app/admin'))

// Collapse the mobile drawer + admin dropdown whenever navigation happens.
watch(() => route.path, () => { mobileOpen.value = false; adminOpen.value = false })

// Close the admin dropdown on an outside click (desktop floating menu).
function onDocClick(e: MouseEvent) {
  if (adminOpen.value && adminDropdown.value && !adminDropdown.value.contains(e.target as Node)) {
    adminOpen.value = false
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

async function handleLogout() {
  await auth.logout()
  router.push('/app/login')
}
</script>

<style scoped>
.shell { display: flex; flex-direction: column; height: 100vh; height: 100dvh; overflow: hidden; }

.topnav {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 0 24px;
  /* Sit clear of the iOS status bar / notch (the PWA uses a black-translucent
     status bar + viewport-fit=cover, so content draws underneath it). The insets
     are 0 on devices without a notch, so desktop is unaffected. content-box keeps
     the 52px bar height while the top inset is added above it. */
  padding-top: env(safe-area-inset-top);
  padding-left: max(24px, env(safe-area-inset-left));
  padding-right: max(24px, env(safe-area-inset-right));
  box-sizing: content-box;
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

/* On desktop the drawer is transparent — its children join the topnav flow. */
.nav-drawer { display: contents; }

.menu-toggle {
  display: none;
  margin-left: auto;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--accent);
  padding: 6px;
  cursor: pointer;
  align-items: center;
  justify-content: center;
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

/* ── Admin dropdown ─────────────────────────────────────────────────────────── */
.admin-dropdown { position: relative; display: flex; align-items: stretch; }
.admin-trigger {
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  font: inherit;
}
.chevron { transition: transform 0.15s; opacity: 0.7; }
.admin-dropdown.open .chevron { transform: rotate(180deg); }

.admin-menu {
  position: absolute;
  top: 100%;
  left: 0;
  min-width: 190px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--glow-sm), var(--shadow);
  padding: 5px;
  z-index: 200;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.admin-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 12px;
  color: var(--text-muted);
  text-decoration: none;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 1px;
  text-transform: uppercase;
  font-family: var(--font-mono);
  border-radius: var(--radius-sm);
  border-left: 2px solid transparent;
  transition: all 0.15s;
}
.admin-item:hover { color: var(--text); background: var(--accent-dim); }
.admin-item.active { color: var(--accent); border-left-color: var(--accent); text-shadow: var(--glow-sm); }

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

.content { flex: 1; overflow-y: auto; padding: 28px; }

/* Shared icon styling — inherit currentColor, no shrink. */
.nav-icon { flex-shrink: 0; }
.nav-link.active .nav-icon { filter: drop-shadow(var(--glow-sm)); }
.logout-btn { display: inline-flex; align-items: center; gap: 5px; }

/* ── Mobile: collapse the nav into a hamburger-toggled drawer ───────────────── */
@media (max-width: 860px) {
  /* Keep the safe-area top/side insets (don't use the `padding` shorthand, which
     would zero out the status-bar offset on exactly the devices that need it). */
  .topnav {
    padding-top: env(safe-area-inset-top);
    padding-bottom: 0;
    padding-left: max(16px, env(safe-area-inset-left));
    padding-right: max(16px, env(safe-area-inset-right));
  }
  .logo { margin-right: 0; }

  .menu-toggle { display: inline-flex; }

  /* The drawer drops below the bar as a full-width panel. */
  .nav-drawer {
    display: none;
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    flex-direction: column;
    align-items: stretch;
    gap: 0;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    box-shadow: var(--shadow);
    padding: 8px 0;
    z-index: 100;
  }
  .nav-drawer.open { display: flex; }

  .nav-links {
    flex-direction: column;
    align-items: stretch;
    height: auto;
    flex: none;
  }
  .nav-link {
    height: 44px;
    border-bottom: none;
    border-left: 2px solid transparent;
    padding: 0 20px;
  }
  .nav-link.active { border-bottom-color: transparent; border-left-color: var(--accent); }

  /* Admin dropdown expands inline within the vertical drawer. */
  .admin-dropdown { flex-direction: column; align-items: stretch; }
  .admin-trigger { height: 44px; border-bottom: none; border-left: 2px solid transparent; }
  .admin-trigger.active { border-left-color: var(--accent); }
  .chevron { margin-left: auto; }
  .admin-menu {
    position: static;
    box-shadow: none;
    border: none;
    border-radius: 0;
    background: var(--surface-2);
    padding: 0;
  }
  .admin-item { height: 42px; padding-left: 40px; border-radius: 0; }

  .nav-right {
    margin-left: 0;
    flex-wrap: wrap;
    gap: 12px;
    padding: 12px 20px 4px;
    border-top: 1px solid var(--border);
    margin-top: 8px;
  }
}

@media (max-width: 640px) {
  .content { padding: 16px; }
}
</style>
