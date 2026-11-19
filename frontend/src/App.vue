<script setup>
// 应用根组件：组件经 main.js 中 app.use(Antd) 全量注册，此处配置中文语境与主题。
// 主题色（素雅灰蓝）两种主题不变；深色启用 antd darkAlgorithm（ADR-0055），
// 偏好真源是 theme store，与 <html data-theme> 驱动的 CSS 变量同一份。
// 账户顶栏提升到壳层：所有业务页右上角共用一个身份/登出/主题入口；
// 登录页（meta.public）不挂，保持全屏无干扰（主题照常生效）。
import { computed } from 'vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { theme as antdTheme } from 'ant-design-vue'
import { useRoute } from 'vue-router'
import AccountBar from './components/AccountBar.vue'
import { useThemeStore } from './stores/theme'

const route = useRoute()
const themeStore = useThemeStore()
const antdConfig = computed(() => ({
  token: { colorPrimary: '#5b7a9d' },
  algorithm: themeStore.theme === 'dark' ? antdTheme.darkAlgorithm : undefined,
}))
</script>

<template>
  <a-config-provider :locale="zhCN" :theme="antdConfig">
    <!-- route.matched.length：首帧导航未解析完时 meta 是空的，不挡会在登录页闪一下顶栏 -->
    <AccountBar v-if="route.matched.length && !route.meta.public" />
    <router-view />
  </a-config-provider>
</template>
