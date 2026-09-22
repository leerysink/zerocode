import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/store/user'
import { collectMenuPaths } from '@/utils/menuPath'
import { appTitle } from '@/config'

const Layout = () => import('@/layout/index.vue')

// 不依赖菜单授权就能访问的路径：首页、个人中心、403 本身与根路径（会 redirect 到首页）
const PUBLIC_PATHS = ['/dashboard', '/profile', '/403', '/']

export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: { title: '登录' },
  },
  {
    path: '/',
    component: Layout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '首页', icon: 'HomeOutlined' },
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/profile/index.vue'),
        meta: { title: '个人中心', icon: 'UserOutlined' },
      },
      {
        path: '403',
        name: 'Forbidden',
        component: () => import('@/views/403/index.vue'),
        meta: { title: '无访问权限' },
      },
    ],
  },
  {
    path: '/system',
    component: Layout,
    redirect: '/system/user',
    meta: { title: '系统管理', icon: 'SettingOutlined' },
    children: [
      {
        path: 'user',
        name: 'SystemUser',
        component: () => import('@/views/system/user/index.vue'),
        meta: { title: '用户管理', icon: 'UserOutlined' },
      },
      {
        path: 'role',
        name: 'SystemRole',
        component: () => import('@/views/system/role/index.vue'),
        meta: { title: '角色管理', icon: 'TeamOutlined' },
      },
      {
        path: 'menu',
        name: 'SystemMenu',
        component: () => import('@/views/system/menu/index.vue'),
        meta: { title: '菜单管理', icon: 'MenuOutlined' },
      },
      {
        path: 'dept',
        name: 'SystemDept',
        component: () => import('@/views/system/dept/index.vue'),
        meta: { title: '部门管理', icon: 'ApartmentOutlined' },
      },
      {
        path: 'operLog',
        name: 'SystemOperLog',
        component: () => import('@/views/system/operLog/index.vue'),
        meta: { title: '操作日志', icon: 'ProfileOutlined' },
      },
      {
        path: 'loginLog',
        name: 'SystemLoginLog',
        component: () => import('@/views/system/loginLog/index.vue'),
        meta: { title: '登录日志', icon: 'LoginOutlined' },
      },
      {
        path: 'dict',
        name: 'SystemDict',
        component: () => import('@/views/system/dict/index.vue'),
        meta: { title: '字典管理', icon: 'BookOutlined' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫
router.beforeEach(async (to, _from, next) => {
  const userStore = useUserStore()
  document.title = (to.meta.title as string) || appTitle

  if (to.path === '/login') {
    if (userStore.token) {
      next('/')
    } else {
      next()
    }
    return
  }

  if (!userStore.token) {
    next('/login')
    return
  }

  if (!userStore.userInfo) {
    try {
      await userStore.fetchUserInfo()
    } catch {
      userStore.reset()
      next('/login')
      return
    }
  }

  // 首次进入或刷新时，确保加载动态菜单
  let menuLoadFailed = false
  if (!userStore.menus || userStore.menus.length === 0) {
    try {
      await userStore.loadRouters()
    } catch {
      menuLoadFailed = true
    }
  }

  // 菜单只控制侧边栏可见性，路由是静态注册的，所以这里再挡一层：
  // 点到未授权路径时跳 403（后端接口级鉴权仍是最终防线）。
  // 菜单没拉成功时不校验：否则一次网络抖动就会把「没取到菜单」误判成「越权」，全站跳 403。
  if (!menuLoadFailed && !PUBLIC_PATHS.includes(to.path)) {
    const allowed = collectMenuPaths(userStore.menus)
    // 允许命中父路径（如 /system 授权了 /system/user 时也能进），与 redirect 行为对齐
    if (!allowed.some((p) => p === to.path || p.startsWith(`${to.path}/`))) {
      next('/403')
      return
    }
  }

  next()
})

export default router
