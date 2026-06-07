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
        name: 'Cove',
        short_name: 'Cove',
        description: 'Ephemeral desktop & browser containers for your home lab.',
        theme_color: '#06060f',
        background_color: '#06060f',
        display: 'standalone',
        start_url: '/',
        scope: '/',
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
        // SPA fallback, but never hijack the API or live workspace streams.
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api/, /^\/workspace/],
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
