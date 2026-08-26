import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import path from 'path'

import { dashboardVersionDefine } from './app-version'

// https://vite.dev/config/
export default defineConfig({
  plugins: [tailwindcss(), react()],
  define: dashboardVersionDefine,
  server: {
    host: '127.0.0.1',
    port: 7999,
    allowedHosts: ['sengokucolad.tail1e46b9.ts.net'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',  // WebUI 后端服务器
        changeOrigin: true,
        ws: true,
        // 确保 Cookie 正确转发
        cookieDomainRewrite: '',  // 移除域名限制
        cookiePathRewrite: '/',   // 确保路径一致
      },
      '/maibot_statistics.html': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  optimizeDeps: {
    include: ['react', 'react-dom'],
  },
  build: {
    chunkSizeWarningLimit: 600,
  },
})
