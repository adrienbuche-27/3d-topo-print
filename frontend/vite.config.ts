import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // MapLibre's worker is an ES module.
  worker: { format: 'es' },
  server: {
    // Forward API calls to the FastAPI backend during development.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
