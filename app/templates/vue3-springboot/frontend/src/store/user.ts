import { defineStore } from 'pinia'
import { ref } from 'vue'
import { login as loginApi, logout as logoutApi, getUserInfo, getRouters, type LoginDTO, type LoginVO } from '@/api/auth'

export interface MenuItem {
  id: number
  parentId: number
  menuName: string
  menuType: string
  path: string
  component: string
  permission: string
  icon: string
  sort: number
  visible: number
  children?: MenuItem[]
}

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem('token') || '')
  const userInfo = ref<LoginVO | null>(null)
  const roles = ref<string[]>([])
  const permissions = ref<string[]>([])
  const menus = ref<MenuItem[]>([])

  async function login(dto: LoginDTO) {
    const res = await loginApi(dto)
    token.value = res.data.token
    localStorage.setItem('token', res.data.token)
    userInfo.value = res.data
    roles.value = res.data.roles
    permissions.value = res.data.permissions
    // 登录成功后加载动态菜单
    await loadRouters()
    return res
  }

  async function fetchUserInfo() {
    const res = await getUserInfo()
    userInfo.value = res.data
    roles.value = res.data.roles
    permissions.value = res.data.permissions
    return res.data
  }

  async function loadRouters() {
    const res = await getRouters()
    menus.value = res.data || []
  }

  async function logout() {
    try {
      await logoutApi()
    } finally {
      reset()
    }
  }

  function reset() {
    token.value = ''
    userInfo.value = null
    roles.value = []
    permissions.value = []
    menus.value = []
    localStorage.removeItem('token')
  }

  function isAdmin(): boolean {
    return roles.value.includes('admin')
  }

  function hasPermission(perm: string): boolean {
    if (isAdmin()) return true
    return permissions.value.includes(perm)
  }

  return {
    token,
    userInfo,
    roles,
    permissions,
    menus,
    login,
    fetchUserInfo,
    loadRouters,
    logout,
    reset,
    isAdmin,
    hasPermission,
  }
})
