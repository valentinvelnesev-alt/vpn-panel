import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    // Дев-сервер шлёт запросы в API, поднятое через docker compose.
    proxy: {
      '/api': { target: 'http://localhost:4250', changeOrigin: true, ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
