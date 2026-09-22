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
    // 本地开发端口，3000 已被同机 snfyxm 占用，改用 3001
    port: 3001,
    proxy: {
      '/api': {
        // 对应 dev 环境的后端端口（application-dev.yml 的 server.port）
        target: 'http://localhost:8082',
        changeOrigin: true,
      },
    },
  },
})
