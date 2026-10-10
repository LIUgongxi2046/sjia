import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    headers: { 'Cache-Control': 'no-store' },
    proxy: {
      '/api': { target: process.env.SJIA_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
    headers: { 'Cache-Control': 'no-store' },
    proxy: {
      '/api': { target: process.env.SJIA_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
});
