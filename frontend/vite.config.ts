import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on :8010 (python -m conceptflow.server). In development the
// page is served by Vite and proxies API, event-stream and media requests.
const backend = process.env.CONCEPTFLOW_API ?? 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5179,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/media': { target: backend, changeOrigin: true },
    },
  },
})
