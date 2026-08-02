import { defineConfig } from 'vite'
import type { ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

/**
 * PRISM dev server.
 *
 * Tailwind v4 is CSS-first: there is no tailwind.config.js and no
 * postcss.config.js in this project on purpose. The whole design system lives
 * in src/index.css behind `@import "tailwindcss"` and a `@theme` block, and the
 * Vite plugin below is the only wiring it needs.
 *
 * The API is a separate FastAPI process on :8000. Every client call is made to
 * a same-origin `/api/...` path and proxied here, so the browser never deals
 * with CORS in development and the production build can be served from the same
 * origin as the API without changing a single URL.
 */
const API_TARGET = process.env.PRISM_API_TARGET || 'http://localhost:8000'

const apiProxy: Record<string, ProxyOptions> = {
  '/api': {
    target: API_TARGET,
    changeOrigin: true,
    // Notifications are pushed over a WebSocket on /api/notifications/stream.
    // Without this the upgrade is proxied as a plain request, the socket never
    // opens, and the client silently falls back to its slow poll - which works,
    // and hides the fact that the fast path is broken.
    ws: true,
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],

  server: {
    port: 5173,
    // The backend's CORS allow-list names 5173 explicitly. Silently sliding to
    // 5174 when the port is busy would produce a confusing CORS failure later,
    // so fail loudly at start-up instead.
    strictPort: true,
    proxy: apiProxy,
  },

  // `npm run preview` serves the production build; it needs the same proxy or
  // every request for a bundle or a document 404s against the static server.
  preview: {
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
    target: 'es2020',
  },
})
