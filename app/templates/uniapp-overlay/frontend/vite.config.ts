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
  // 移动端工程住在 ./mobile/，是独立工程（独立 package.json / 独立 node_modules）。
  // 不锁入口时 vite 的依赖预构建会把 mobile/index.html 当成自己的第二个 HTML 入口去扫，
  // 用它解析不出来的 @dcloudio/* —— 管理端 dev 起不来或白等预构建。锁成管理端自己这一份。
  optimizeDeps: {
    entries: ['index.html'],
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
      // 移动端 H5：dev 期由 compose 里的 mobile 服务（uni-app dev server）承载，只挂内部网。
      // ws 必须开：页面热更新走这条 websocket（/h5/ 是客户端算出来的 HMR 路径）。
      // 故意不改 Origin/Host：vite 的 DNS-rebinding 守卫只放行 localhost 与 IP，改写成
      // `mobile` 这类服务名会被新版本 vite 直接 403，而保留原 Host 正好落在白名单里。
      '/h5': {
        target: 'http://mobile:8080',
        ws: true,
      },
    },
  },
})
