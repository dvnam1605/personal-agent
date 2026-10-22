import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        // Libraries change rarely: separate chunks keep the app chunk small and cacheable.
        codeSplitting: {
          groups: [
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: 'temml', test: /node_modules[\\/]temml[\\/]/ },
            { name: 'icons', test: /node_modules[\\/]lucide-react[\\/]/ },
            { name: 'vendor', test: /node_modules[\\/]/ },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/query': 'http://127.0.0.1:8000',
      '/approvals': 'http://127.0.0.1:8000',
      '/questions': 'http://127.0.0.1:8000',
      '/auth': 'http://127.0.0.1:8000',
      '/conversations': 'http://127.0.0.1:8000',
      '/documents': 'http://127.0.0.1:8000',
      '/runs': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/ready': 'http://127.0.0.1:8000',
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
