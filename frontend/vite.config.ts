import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The frontend talks to the API on the same origin, so a proxy in development
// keeps the deployed and local URLs identical and avoids CORS entirely.
export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
