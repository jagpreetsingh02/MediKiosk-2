import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The API is proxied so every surface is same-origin in development. A kiosk that needs CORS
// configured in order to work is a kiosk that stops working at a venue.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/mock-idp': { target: 'http://localhost:8000', changeOrigin: true },
      '/about': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
});
