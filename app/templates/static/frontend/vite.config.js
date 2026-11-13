import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 纯前端栈没有后端，因此没有 proxy 段：任何 /api 请求都不存在可转发的 upstream。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
})
