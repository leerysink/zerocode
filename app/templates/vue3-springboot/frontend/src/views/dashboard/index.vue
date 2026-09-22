<template>
  <div class="dashboard">
    <a-card :bordered="false" class="welcome-card">
      <div class="welcome">
        <a-avatar :size="56" class="welcome-avatar">
          {{ nickname.charAt(0) }}
        </a-avatar>
        <div class="welcome-text">
          <h2>{{ greeting }}，{{ nickname }}</h2>
          <p>欢迎使用{{ appTitle }}，当前角色：
            <a-tag v-for="r in roles" :key="r" color="blue">{{ r }}</a-tag>
            <a-tag v-if="roles.length === 0">无</a-tag>
          </p>
        </div>
      </div>
    </a-card>

    <a-row :gutter="16" class="stat-row">
      <a-col :span="8">
        <a-card :bordered="false">
          <a-statistic title="拥有权限数" :value="permissions.length" />
        </a-card>
      </a-col>
      <a-col :span="8">
        <a-card :bordered="false">
          <a-statistic title="可见菜单数" :value="menuCount" />
        </a-card>
      </a-col>
      <a-col :span="8">
        <a-card :bordered="false">
          <a-statistic title="账号" :value="username" :value-style="{ fontSize: '20px', lineHeight: '30px' }" />
        </a-card>
      </a-col>
    </a-row>

    <a-card title="环境信息" :bordered="false" class="env-card">
      <a-descriptions :column="2" size="small">
        <a-descriptions-item label="前端">Vue 3 + Vite + Ant Design Vue</a-descriptions-item>
        <a-descriptions-item label="后端">Spring Boot 3 + MyBatis-Plus</a-descriptions-item>
        <a-descriptions-item label="数据库">PostgreSQL</a-descriptions-item>
        <a-descriptions-item label="缓存">Redis</a-descriptions-item>
      </a-descriptions>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useUserStore } from '@/store/user'
import { appTitle } from '@/config'

const userStore = useUserStore()
const nickname = computed(() => userStore.userInfo?.nickname || userStore.userInfo?.username || '用户')
const username = computed(() => userStore.userInfo?.username || '-')
const roles = computed(() => userStore.roles)
const permissions = computed(() => userStore.permissions)
/**
 * /auth/routers 返回的是扁平列表（后端只取 menu_type in ('M','C') 且 status=1，
 * 层级靠 parent_id 表达，children 字段不会有值），所以 length 就是全部可见菜单数。
 */
const menuCount = computed(() => userStore.menus.length)

// 必须是 computed 而不是普通函数：模板里写 {{ greeting }} 时，
// Vue 的 toDisplayString 对函数走 String(fn)，会把函数源码整段渲染到页面上。
const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
})
</script>

<style scoped>
.welcome {
  display: flex;
  align-items: center;
  gap: 18px;
}

.welcome-avatar {
  background: linear-gradient(135deg, #10b981, #0d9488);
  color: #fff;
  font-size: 22px;
}

.welcome-text h2 {
  margin: 0 0 6px;
  font-size: 20px;
}

.welcome-text p {
  margin: 0;
  color: rgba(0, 0, 0, 0.55);
}

.stat-row {
  margin-top: 16px;
}

.env-card {
  margin-top: 16px;
}
</style>
