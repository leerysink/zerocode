<template>
  <a-layout class="layout">
    <a-layout-sider v-model:collapsed="collapsed" :trigger="null" collapsible width="224px" class="sider">
      <div class="logo">
        <div class="logo-icon">
          <SettingOutlined />
        </div>
        <transition name="fade">
          <span v-if="!collapsed" class="logo-title">{{ appTitle }}</span>
        </transition>
      </div>
      <a-menu
        v-model:selectedKeys="selectedKeys"
        v-model:openKeys="openKeys"
        theme="dark"
        mode="inline"
        :items="menuItems"
        @click="onMenuClick"
      />
    </a-layout-sider>

    <a-layout class="main">
      <a-layout-header class="header">
        <div class="header-left">
          <component
            :is="collapsed ? MenuUnfoldOutlined : MenuFoldOutlined"
            class="trigger"
            @click="collapsed = !collapsed"
          />
          <a-breadcrumb class="breadcrumb">
            <a-breadcrumb-item v-for="(item, index) in breadcrumb" :key="item">
              <span :class="{ 'current': index === breadcrumb.length - 1 }">{{ item }}</span>
            </a-breadcrumb-item>
          </a-breadcrumb>
        </div>
        <div class="header-right">
          <a-tooltip title="刷新当前页" placement="bottom">
            <ReloadOutlined class="header-icon" @click="reloadPage" />
          </a-tooltip>
          <a-tooltip title="全屏" placement="bottom">
            <FullscreenOutlined class="header-icon" @click="toggleFullscreen" />
          </a-tooltip>
          <a-divider type="vertical" />
          <a-dropdown>
            <span class="user-info">
              <a-avatar class="user-avatar">
                {{ userStore.userInfo?.nickname?.charAt(0) || 'U' }}
              </a-avatar>
              <span class="user-name">{{ userStore.userInfo?.nickname || userStore.userInfo?.username }}</span>
              <CaretDownOutlined class="user-caret" />
            </span>
            <template #overlay>
              <a-menu class="user-dropdown">
                <a-menu-item @click="handleProfile">
                  <UserOutlined />
                  <span>个人中心</span>
                </a-menu-item>
                <a-menu-divider />
                <a-menu-item @click="handleLogout">
                  <LogoutOutlined />
                  <span>退出登录</span>
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </div>
      </a-layout-header>

      <a-layout-content class="content app-content">
        <router-view v-slot="{ Component, route }">
          <transition name="fade-slide" mode="out-in">
            <component :is="Component" :key="route.fullPath + '-' + refreshKey" />
          </transition>
        </router-view>
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script setup lang="ts">
import { ref, computed, watch, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as AntIcons from '@ant-design/icons-vue'
import { useUserStore, type MenuItem } from '@/store/user'
import { buildMenuFullPath } from '@/utils/menuPath'
import { appTitle } from '@/config'
import { Modal } from 'ant-design-vue'
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ReloadOutlined,
  FullscreenOutlined,
  CaretDownOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
} from '@ant-design/icons-vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const collapsed = ref(false)
const selectedKeys = ref<string[]>([])
const openKeys = ref<string[]>([])
// 刷新当前页时自增，参与页面组件的 key 计算，促使其重新挂载
const refreshKey = ref(0)

const dynamicMenuTree = computed(() => {
  const flat = userStore.menus || []
  // 路径拼接统一走 buildMenuFullPath（后端下发的是扁平列表，children 恒为空），
  // 路由守卫的允许集合用的是同一个函数，保证侧边栏 key 与校验结果不会漂移
  const fullPath = buildMenuFullPath(flat)
  const wrap = (m: MenuItem) => ({ ...m, fullPath: fullPath.get(m.id) || '' })
  const roots = flat
    .filter((m) => m.parentId === 0 && (m.menuType === 'M' || m.menuType === 'C'))
    .map(wrap)
  for (const root of roots) {
    const children = flat
      .filter((c) => c.parentId === root.id && c.menuType === 'C')
      .map(wrap)
    if (children.length > 0) {
      root.children = children as any
    }
  }
  const sortFn = (a: any, b: any) => (a.sort || 0) - (b.sort || 0)
  roots.sort(sortFn)
  roots.forEach((r: any) => r.children?.sort(sortFn))
  return roots
})

const menuItems = computed(() => {
  return dynamicMenuTree.value.map((r: any) => {
    const icon = r.icon ? getIcon(r.icon) : null
    return {
      key: r.fullPath,
      label: r.menuName,
      icon: icon ? h(icon) : undefined,
      children: r.children?.map((c: any) => {
        const cIcon = c.icon ? getIcon(c.icon) : null
        return {
          key: c.fullPath,
          label: c.menuName,
          icon: cIcon ? h(cIcon) : undefined,
        }
      }),
    }
  })
})

function getIcon(name: string) {
  return (AntIcons as any)[name] || AntIcons.AppstoreOutlined
}

const breadcrumb = computed(() => {
  const matched = route.matched.filter((r) => r.meta?.title)
  return matched.map((r) => r.meta?.title as string)
})

watch(
  () => route.path,
  (path) => {
    selectedKeys.value = [path]
    const top = '/' + path.split('/').filter(Boolean)[0]
    if (top && !openKeys.value.includes(top)) {
      openKeys.value = Array.from(new Set([...openKeys.value, top]))
    }
  },
  { immediate: true }
)

function onMenuClick({ key }: { key: string }) {
  router.push(key)
}

function handleLogout() {
  Modal.confirm({
    title: '确认退出登录?',
    icon: h(LogoutOutlined, { style: 'color: #ef4444' }),
    okButtonProps: { danger: true },
    onOk: async () => {
      await userStore.logout()
      router.push('/login')
    },
  })
}

function handleProfile() {
  router.push('/profile')
}

function reloadPage() {
  refreshKey.value++
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen()
  } else {
    document.exitFullscreen()
  }
}
</script>

<style scoped>
.layout {
  height: 100vh;
}
.main {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-page);
}

/* Sider 渐变侧边栏 */
:deep(.sider.ant-layout-sider) {
  background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%) !important;
}
/* 侧边栏固定高度，菜单区域内部滚动，避免溢出到浅色布局背景上 */
:deep(.sider .ant-layout-sider-children) {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
:deep(.sider .ant-menu-root) {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}
:deep(.sider .ant-menu-root)::-webkit-scrollbar {
  width: 6px;
}
:deep(.sider .ant-menu-root)::-webkit-scrollbar-track {
  background: transparent;
}
:deep(.sider .ant-menu-root)::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 3px;
}
:deep(.sider .ant-menu-root)::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.3);
}
.logo {
  height: 64px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  background: linear-gradient(135deg, rgba(29, 78, 255, 0.18) 0%, rgba(76, 174, 254, 0.12) 100%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.5px;
  overflow: hidden;
}
.logo-icon {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  background: var(--primary-gradient);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  color: #fff;
  box-shadow: 0 4px 12px rgba(29, 78, 255, 0.4);
  flex-shrink: 0;
}
.logo-title {
  white-space: nowrap;
  overflow: hidden;
  font-weight: 600;
}
:deep(.ant-menu-dark) {
  background: transparent !important;
}
:deep(.ant-menu-dark .ant-menu-sub) {
  background: rgba(0, 0, 0, 0.2) !important;
}
:deep(.ant-menu-dark .ant-menu-item-selected) {
  background: var(--primary-gradient) !important;
}

/* Header 顶部 */
.header {
  background: #fff;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  box-shadow: 0 1px 0 rgba(0, 0, 0, 0.04), 0 4px 12px rgba(15, 23, 42, 0.03);
  position: sticky;
  top: 0;
  z-index: 9;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.trigger {
  font-size: 18px;
  cursor: pointer;
  padding: 8px;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
  color: var(--text-secondary);
}
.trigger:hover {
  color: var(--primary-color);
  background: #f0f5ff;
}
.breadcrumb {
  font-size: 13px;
}
.breadcrumb :deep(.current) {
  color: var(--primary-color);
  font-weight: 500;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-icon {
  font-size: 16px;
  color: var(--text-secondary);
  padding: 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.header-icon:hover {
  color: var(--primary-color);
  background: #f0f5ff;
}
.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  cursor: pointer;
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
}
.user-info:hover {
  background: #f0f5ff;
}
.user-avatar {
  background: var(--primary-gradient) !important;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(29, 78, 255, 0.25);
}
.user-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}
.user-caret {
  font-size: 10px;
  color: var(--text-muted);
}

.content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 5px;
  overflow: hidden;
}

/* 路由过渡 */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.3s ease;
}
.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* 下拉菜单 */
:deep(.user-dropdown .ant-menu-item) {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
}
:deep(.user-dropdown) {
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
}
</style>
