import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/',
  envPrefix: ['VITE_', 'APP_'],
  plugins: [react()],
  publicDir: 'public',
  build: {
    outDir: process.env.VITE_BUILD_OUTDIR || path.resolve(__dirname, '..', 'backend', 'static'),
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    host: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:9000',
        changeOrigin: true,
      },
      '/health': {
        target: process.env.VITE_API_URL || 'http://localhost:9000',
        changeOrigin: true,
      },
    },
  },
});
