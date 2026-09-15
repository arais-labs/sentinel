import { fileURLToPath } from 'node:url';
import { defineConfig } from 'electron-vite';
import react from '@vitejs/plugin-react';

const resolve = (relative: string) => fileURLToPath(new URL(relative, import.meta.url));

export default defineConfig({
  main: {
    build: {
      outDir: 'dist/main',
      rolldownOptions: { external: ['ws'] },
      lib: { entry: resolve('./src/main/main.ts'), formats: ['es'], fileName: () => 'main.js' },
    },
  },
  preload: {
    build: {
      outDir: 'dist/preload',
      lib: { entry: resolve('./src/preload/preload.ts'), formats: ['es'], fileName: () => 'preload.mjs' },
    },
  },
  renderer: {
    root: resolve('../../frontend/sentinel'),
    // Keep desktop and standalone web dependency caches independent.
    cacheDir: resolve('./node_modules/.vite/renderer'),
    envPrefix: ['VITE_', 'APP_'],
    plugins: [react()],
    css: { postcss: resolve('../../frontend/sentinel') },
    server: { host: '127.0.0.1', port: 5173 },
    build: {
      outDir: resolve('./dist/renderer'),
      rolldownOptions: {
        input: {
          app: resolve('../../frontend/sentinel/index.html'),
        },
      },
    },
  },
});
