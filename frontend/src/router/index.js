import { createRouter, createWebHistory } from 'vue-router'

// 路由表：/ 对话页、/settings 设置页（视图均懒加载，按需分包）
const routes = [
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
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
