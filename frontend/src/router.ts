import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
    { path: '/setup', component: () => import('@/views/SetupView.vue'), meta: { public: true } },
    { path: '/', component: () => import('@/views/DashboardView.vue') },
    { path: '/workspace/:id', component: () => import('@/views/WorkspaceView.vue') },
    { path: '/files', component: () => import('@/views/FilesView.vue') },
    { path: '/preferences', component: () => import('@/views/PreferencesView.vue') },
    { path: '/admin/users', component: () => import('@/views/AdminUsersView.vue'), meta: { admin: true } },
    { path: '/admin/sessions', component: () => import('@/views/AdminSessionsView.vue'), meta: { admin: true } },
    { path: '/admin/images', component: () => import('@/views/AdminImagesView.vue'), meta: { admin: true } },
    { path: '/admin/audit', component: () => import('@/views/AdminAuditView.vue'), meta: { admin: true } },
    { path: '/admin/settings', component: () => import('@/views/AdminSettingsView.vue'), meta: { admin: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

let initialized = false

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (!initialized) {
    await auth.init()
    initialized = true
  }

  if (auth.needsSetup && to.path !== '/setup') return '/setup'
  if (!auth.needsSetup && to.path === '/setup') return '/'

  if (!to.meta.public && !auth.isAuthenticated) return '/login'
  if (to.path === '/login' && auth.isAuthenticated) return '/'

  if (to.meta.admin && !auth.isAdmin) return '/'
})

export { router }
