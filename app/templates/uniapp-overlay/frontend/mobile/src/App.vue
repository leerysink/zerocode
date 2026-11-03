<script setup>
import { onLaunch } from '@dcloudio/uni-app'

// 需要登录的页面走这里的拦截器。接口一律写相对 /api：H5 与后台管理系统同源（ADR-0039），
// 相对地址在 dev（vite 代理）和 prod（nginx 反代）两条链路下都不必改一行代码。
const AUTH_FREE = ['pages/index/index', 'pages/login/index', 'pages/register/index']

onLaunch(() => {
  ['navigateTo', 'redirectTo', 'switchTab', 'reLaunch'].forEach((api) => {
    uni.addInterceptor(api, {
      invoke(args) {
        const path = String(args.url || '').split('?')[0].replace(/^\//, '')
        if (AUTH_FREE.includes(path) || uni.getStorageSync('token')) return true
        uni.showToast({ title: '请先登录', icon: 'none' })
        uni.navigateTo({ url: '/pages/login/index' })
        return false
      },
    })
  })
})
</script>

<style>
page { background-color: #f7f8fa; }
</style>
