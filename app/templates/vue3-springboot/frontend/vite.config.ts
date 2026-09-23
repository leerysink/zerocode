import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { AntDesignVueResolver } from 'unplugin-vue-components/resolvers'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    Components({
      resolvers: [
        AntDesignVueResolver({
          importStyle: false,
          resolveIcons: true,
        }),
      ],
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    // 容器内监听：host 必须 0.0.0.0，端口固定 5173（宿主端口由平台 compose 映射）
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // backend 是 compose 服务名；后端 context-path 已是 /api，故不写 rewrite
      // 上传件回读也走这一条（后端返回的 URL 自带 /api 前缀），无需 /uploads 特例
      '/api': {
        target: 'http://backend:8080',
        changeOrigin: true,
      },
    },
  },
})
