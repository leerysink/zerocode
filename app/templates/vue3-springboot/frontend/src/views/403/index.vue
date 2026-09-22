<template>
  <a-card :bordered="false" class="forbidden-card">
    <a-result status="403" title="403" sub-title="你没有这个页面的访问权限，需要管理员在「角色管理」里给它勾上对应菜单。">
      <template #extra>
        <a-button type="primary" @click="goHome">返回首页</a-button>
      </template>
    </a-result>
  </a-card>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function goHome() {
  router.push('/dashboard')
}
</script>

<style scoped>
.forbidden-card {
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
