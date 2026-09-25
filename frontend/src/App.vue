<script setup>
// 应用根组件：组件经 main.js 中 app.use(Antd) 全量注册，此处仅配置中文语境与主题色（素雅灰蓝）。
// 账户顶栏提升到壳层：所有业务页右上角共用一个身份/登出入口；
// 登录页（meta.public）不挂，保持全屏无干扰。
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { useRoute } from 'vue-router'
import AccountBar from './components/AccountBar.vue'

const route = useRoute()
</script>

<template>
  <a-config-provider :locale="zhCN" :theme="{ token: { colorPrimary: '#5b7a9d' } }">
    <!-- route.matched.length：首帧导航未解析完时 meta 是空的，不挡会在登录页闪一下顶栏 -->
    <AccountBar v-if="route.matched.length && !route.meta.public" />
    <router-view />
  </a-config-provider>
</template>
