<script>
export const NAV_RAIL_WIDTH = 64
</script>
<script setup>
import { AppstoreOutlined, SettingOutlined, ShopOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
</script>

<template>
  <nav class="zc-rail" aria-label="主导航">
    <router-link to="/apps"><appstore-outlined />应用</router-link>
    <router-link to="/store"><shop-outlined />商店</router-link>
    <router-link to="/settings"><setting-outlined />设置</router-link>
    <router-link to="/skills"><thunderbolt-outlined />技能</router-link>
  </nav>
</template>

<style scoped>
/* 宽度须与 NAV_RAIL_WIDTH 一致：ChatView 拿它算手柄定位与拖拽上限 */
.zc-rail {
  flex: 0 0 auto;
  width: 64px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 6px;
  box-sizing: border-box;
  background: var(--zc-sidebar-bg);
  border-right: 1px solid var(--zc-border);
}
.zc-rail a {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 8px 0;
  border-radius: 6px;
  color: var(--zc-text-sub);
  font-size: 11px;
  text-decoration: none;
}
.zc-rail .anticon {
  font-size: 16px;
}
.zc-rail a:hover {
  background: var(--zc-bg);
  color: var(--zc-primary);
}
.zc-rail a.router-link-active {
  background: rgba(var(--zc-primary-rgb), 0.12);
  color: var(--zc-primary);
  font-weight: 500;
}
</style>