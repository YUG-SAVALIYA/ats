import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: true,
    allowedHosts: [
      'misprincipled-dumpish-chelsie.ngrok-free.dev',
      '.ngrok-free.dev',
      '.ngrok.io',
      'localhost',
    ],
    proxy: {
      '/api': {
        target: 'http://localhost:8005',
        changeOrigin: true,
      },
    },
  },
});
