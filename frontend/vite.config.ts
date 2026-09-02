import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The API is proxied so every surface is same-origin in development. A kiosk that needs CORS
// configured in order to work is a kiosk that stops working at a venue.
// Ports are configurable because several copies of this project sit side by side and the
// defaults collide. start.sh picks free ones and passes them in; nothing changes when it
// is run directly.
const apiTarget = process.env.VITE_API_TARGET ?? 'http://localhost:10101';
const webPort = Number(process.env.VITE_PORT ?? 10100);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: webPort,
    // Fail loudly instead of silently hopping to the next port: a kiosk proxied at a port
    // nobody printed is worse than a refusal to start.
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/mock-idp': { target: apiTarget, changeOrigin: true },
      '/about': { target: apiTarget, changeOrigin: true },
    },
  },
});
