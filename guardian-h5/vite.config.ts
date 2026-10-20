import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig(({ mode }) => {
  let localTestAccount: { username: string; password: string; patientId: string } | null = null;
  if (mode === 'local-validation') {
    const username = process.env.VITE_LOCAL_TEST_USERNAME;
    const password = process.env.VITE_LOCAL_TEST_PASSWORD;
    const patientId = process.env.VITE_LOCAL_TEST_PATIENT_ID;
    if (!username?.trim() || !password || !patientId || !/^[0-9a-f-]{36}$/.test(patientId)) throw new Error('本地验收需要提供测试账号、密码和已授权患儿标识。');
    localTestAccount = { username, password, patientId };
  }
  return {
  define: { __SJIA_LOCAL_TEST_ACCOUNT__: JSON.stringify(localTestAccount) },
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
  };
});
