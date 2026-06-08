import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      // 'prompt' so a new deploy surfaces a "Reload" pill instead of either
      // silently reloading mid-task or (on iOS standalone) only updating on a
      // full close/reopen. We register the SW ourselves (src/pwa.ts) to add
      // periodic + on-foreground update checks.
      registerType: 'prompt',
      injectRegister: false,
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        // The dashboard app is scoped to /app so it doesn't swallow the
        // per-workspace apps at /workspace/* — browsers won't install a second
        // PWA whose scope sits inside an already-installed one, so the two scopes
        // must not overlap.
        id: '/app',
        name: 'Cove',
        short_name: 'Cove',
        description: 'Ephemeral desktop & browser containers for your home lab.',
        theme_color: '#06060f',
        background_color: '#06060f',
        display: 'standalone',
        start_url: '/app',
        scope: '/app',
        icons: [
          { src: '/pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: '/pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          {
            src: '/pwa-maskable-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
          { src: '/favicon.svg', sizes: 'any', type: 'image/svg+xml' },
        ],
      },
      workbox: {
        // SPA fallback for app routes, but never hijack the API or a live
        // workspace stream. In subpath mode a stream lives at
        // /workspace/{public_id}/ (32-hex id); the SPA's own /workspace/:id
        // (numeric) route still needs the index.html fallback, so only deny the
        // hex-id stream paths.
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api/, /^\/workspace\/[0-9a-f]{16,}/],
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
})
