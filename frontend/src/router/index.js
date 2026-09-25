import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

// 路由表：/login 登录注册（public）；/ 对话页；/settings 设置页；/store 应用商店（视图均懒加载，按需分包）
const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    name: 'chat',
    component: () => import('../views/ChatView.vue'),
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/SettingsView.vue'),
  },
  {
    path: '/apps',
    name: 'apps',
    component: () => import('../views/AppsView.vue'),
  },
  {
    path: '/store',
    name: 'store',
    component: () => import('../views/StoreView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局登录守卫（Ticket 002）：首次导航先查 me；未登录一律导向 /login，已登录不进 /login。
// pinia 在 main.js 先于 router 安装，守卫运行时 store 已可用。
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  const auth = useAuthStore()
  if (!auth.loaded) await auth.fetchMe()
  if (!auth.user) return { path: '/login', query: to.fullPath === '/' ? {} : { next: to.fullPath } }
  return true
})

export default router
