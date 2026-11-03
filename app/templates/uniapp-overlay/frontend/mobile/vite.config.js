import { defineConfig } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'

// 监听 0.0.0.0:8080 且 strictPort：宿主端口由平台 compose 决定，对外只有管理端那一个 dev 端口，
// 手机是经管理端 vite 的 /h5 代理打到这里的（目标端口写死 8080）。端口被占时静默挪到 8081
// 会让代理打到空气上——表现是「预览能开、H5 一片 502」，所以要它当场起不来。
export default defineConfig({
  plugins: [uni()],
  server: {
    host: '0.0.0.0',
    port: 8080,
    strictPort: true,
  },
})
