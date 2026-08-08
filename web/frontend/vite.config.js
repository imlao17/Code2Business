import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    // Element Plus is registered on demand; the remaining entry chunk is
    // ~590 kB minified (~207 kB gzip), while heavy ECharts stays route-lazy.
    chunkSizeWarningLimit: 650
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8123', changeOrigin: true }
    }
  }
})
