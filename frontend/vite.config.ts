import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    watch: {
      usePolling: true, // load-bearing under WSL2/Podman bind mounts
      interval: 300,
    },
  },
  build: {
    // Multi-page build: the MSAL v5 redirect bridge is a second HTML entry
    // point Rollup must emit (dist/redirect.html), served by nginx at the SPA
    // root. It is NOT a feature module — it loads only the bridge script.
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        redirect: resolve(__dirname, 'redirect.html'),
      },
    },
  },
});
