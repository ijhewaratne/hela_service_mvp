
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Dev-only CSP mirrors production intent: scripts only from self, API only on the local backend.
    headers: {
      'Content-Security-Policy':
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
        "connect-src 'self' http://127.0.0.1:8000 ws://127.0.0.1:5173; img-src 'self' blob: data:; " +
        "frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
      'Referrer-Policy': 'no-referrer',
    },
  },
  build: { sourcemap: false },
  test: { environment: 'jsdom', globals: true, setupFiles: ['src/test/setup.ts'] },
});
