<script setup>
// 应用根组件：组件经 main.js 中 app.use(Antd) 全量注册，此处仅配置中文语境与主题色（素雅灰蓝）
import zhCN from 'ant-design-vue/es/locale/zh_CN'
</script>

<template>
  <a-config-provider :locale="zhCN" :theme="{ token: { colorPrimary: '#5b7a9d' } }">
    <router-view />
  </a-config-provider>
</template>
