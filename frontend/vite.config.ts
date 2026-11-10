import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import electron from 'vite-plugin-electron';
import renderer from 'vite-plugin-electron-renderer';
import path from 'path';

export default defineConfig(({ mode }) => ({
  base: './',
  plugins: [
    react(),
    ...(mode === 'web' ? [] : [electron([
      {
        // Entry point of the Electron App
        entry: 'electron/main.ts',
      },
    ]),
    renderer()]),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // Proxy is still useful for web-only dev, but Electron handles this differently
    proxy: {
      '/rpc': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
        secure: false,
      },
    },
  },
}));
