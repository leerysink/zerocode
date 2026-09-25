<script setup>
// 顶栏账户区（原 SideBar 页脚账户行迁移至此）：昵称回显 + 登出，常驻页面右上角。
// 身份单源 /api/auth/me，与商店页同一接缝；authMe 在拦截器豁免名单里，
// 未登录不会自动跳，这里收到 401（被踢/会话过期）时兜底引导回登录页。
import { onMounted, ref } from 'vue'
import { LogoutOutlined, UserOutlined } from '@ant-design/icons-vue'
import { authLogout, authMe } from '../api'

const me = ref(null)
async function loadMe() {
  try {
    me.value = (await authMe()).data
  } catch (e) {
    if (e?.response?.status === 401) window.location.assign('/login')
  }
}
async function doLogout() {
  try { await authLogout() } catch { /* 服务端本就无会话：当作登出成功 */ }
  window.location.assign('/login') // token 已轮换，留在原地只会处处 401
}
onMounted(loadMe)
</script>

<template>
  <a-layout-header class="zc-account-bar">
    <span class="zc-account-name" data-test="account-nickname"><user-outlined /> {{ me?.nickname || '' }}</span>
    <a class="zc-logout" data-test="logout-btn" @click="doLogout"><logout-outlined /> 登出</a>
  </a-layout-header>
</template>

<style scoped>
.zc-account-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  height: 40px;
  line-height: 40px;
  padding: 0 16px;
  background: var(--zc-sidebar-bg);
  border-bottom: 1px solid var(--zc-border);
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-account-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.zc-logout {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  color: var(--zc-text-sub);
}
.zc-logout:hover {
  color: var(--zc-primary);
}
</style>
