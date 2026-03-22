import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/',
  envPrefix: ['VITE_', 'APP_'],
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api/v1': {
        target: 'http://sentinel-backend:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://sentinel-backend:8000',
        changeOrigin: true,
      },
      '/platform': {
        target: 'http://sentinel-backend:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://sentinel-backend:8000',
        changeOrigin: true,
        ws: true,
      },
      '/vnc': {
        target: 'ws://sentinel-backend:8000',
        changeOrigin: true,
        ws: true,
      },
      '/health': {
        target: 'http://sentinel-backend:8000',
        changeOrigin: true,
      },
    },
  },
});
