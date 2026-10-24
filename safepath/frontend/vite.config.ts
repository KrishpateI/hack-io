import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import serviceWorkerManifest from './vite.sw-plugin.mjs';

export default defineConfig({
  plugins: [react(), tailwindcss(), serviceWorkerManifest()],
  // Wording lint reads backend explanation templates as raw text (see wordingLint.test.ts).
  // MapLibre 6 starts its worker from a sibling module URL; pre-bundling breaks that path in dev.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    fs: { allow: ['..'] },
    // Dev only: same-origin access to the API, WebSocket and SSE (override with VITE_API_BASE_URL).
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
      '/sse': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});

