import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    // Dashboard app — lives under /app so its PWA scope (/app) doesn't swallow
    // the per-workspace apps at /workspace/*. That separation lets the main Cove
    // app and individual workspace apps install side-by-side (browsers refuse to
    // install a second PWA whose scope sits inside an already-installed one).
    { path: '/app/login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
    { path: '/app/setup', component: () => import('@/views/SetupView.vue'), meta: { public: true } },
    { path: '/app', component: () => import('@/views/DashboardView.vue') },
    { path: '/app/files', component: () => import('@/views/FilesView.vue') },
    { path: '/app/preferences', component: () => import('@/views/PreferencesView.vue') },
    { path: '/app/docs', component: () => import('@/views/DocsView.vue') },
    { path: '/app/docs/:slug', component: () => import('@/views/DocsView.vue') },
    { path: '/app/admin/users', component: () => import('@/views/AdminUsersView.vue'), meta: { admin: true } },
    { path: '/app/admin/sessions', component: () => import('@/views/AdminSessionsView.vue'), meta: { admin: true } },
    { path: '/app/admin/images', component: () => import('@/views/AdminImagesView.vue'), meta: { admin: true } },
    { path: '/app/admin/zones', component: () => import('@/views/AdminZonesView.vue'), meta: { admin: true } },
    { path: '/app/admin/audit', component: () => import('@/views/AdminAuditView.vue'), meta: { admin: true } },
    { path: '/app/admin/settings', component: () => import('@/views/AdminSettingsView.vue'), meta: { admin: true } },

    // Viewing a node *inside* the dashboard app: in-scope (/app) so navigating
    // here from the grid doesn't trip the browser's out-of-scope "in-app browser"
    // bar. Same component as the standalone entry below.
    { path: '/app/workspace/:id', component: () => import('@/views/WorkspaceView.vue') },

    // Per-workspace app entry — kept at the root so each workspace's PWA scope
    // (/workspace/{id}) sits outside the dashboard app's /app scope and installs
    // as its own app. This is the per-workspace manifest's start_url.
    { path: '/workspace/:id', component: () => import('@/views/WorkspaceView.vue') },

    // Back-compat: redirect the old root-level paths to their /app homes so
    // existing bookmarks and deep links keep working.
    { path: '/', redirect: '/app' },
    { path: '/login', redirect: '/app/login' },
    { path: '/setup', redirect: '/app/setup' },
    { path: '/files', redirect: '/app/files' },
    { path: '/preferences', redirect: '/app/preferences' },
    { path: '/admin/:rest(.*)*', redirect: to => `/app/admin/${(to.params.rest as string[] | undefined)?.join('/') ?? ''}` },
    { path: '/:pathMatch(.*)*', redirect: '/app' },
  ],
})

let initialized = false

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (!initialized) {
    await auth.init()
    initialized = true
  }

  if (auth.needsSetup && to.path !== '/app/setup') return '/app/setup'
  if (!auth.needsSetup && to.path === '/app/setup') return '/app'

  if (!to.meta.public && !auth.isAuthenticated) return '/app/login'
  if (to.path === '/app/login' && auth.isAuthenticated) return '/app'

  if (to.meta.admin && !auth.isAdmin) return '/app'
})

export { router }
