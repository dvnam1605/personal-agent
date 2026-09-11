import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/query': 'http://127.0.0.1:8000',
      '/approvals': 'http://127.0.0.1:8000',
      '/questions': 'http://127.0.0.1:8000',
      '/auth': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/ready': 'http://127.0.0.1:8000',
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
