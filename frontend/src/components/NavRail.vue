<script>
export const NAV_RAIL_WIDTH = 64
</script>
<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { AppstoreOutlined, AuditOutlined, SettingOutlined, ShopOutlined, TeamOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
import * as api from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const isFounder = computed(() => !!auth.user?.is_founder)

// 待审批红点：审批队列已收口运营台（ADR-0062 §7），窄栏上留一个数当入口提示，
// 免得创始人要先进管理页才知道有活。非创始人不发这个请求（收件箱对他也必然是空的）。
const pendingReview = ref(0)
async function refreshPending () {
  try {
    pendingReview.value = (await api.storeApplications('review')).data.length
  } catch {
    pendingReview.value = 0 // 拉不到就不点红点：宁可少提示，不要提示一个看不见的队列
  }
}
watch(isFounder, (on) => { if (on) refreshPending() }, { immediate: true })
defineExpose({ refreshPending, pendingReview })
</script>

<template>
  <nav class="zc-rail" aria-label="主导航">
    <router-link to="/apps"><appstore-outlined />应用</router-link>
    <router-link to="/store"><shop-outlined />商店</router-link>
    <router-link v-if="isFounder" to="/store-admin" data-test="rail-admin">
      <audit-outlined />管理
      <span v-if="pendingReview" class="zc-rail-badge" data-test="rail-admin-badge"
            :title="`${pendingReview} 条申请待审批`">{{ pendingReview > 9 ? '9+' : pendingReview }}</span>
    </router-link>
    <!-- 治理台（仅管理员）：对别人的账号做事，与「管理」（管商店版本行）主语不同 -->
    <router-link v-if="isFounder" to="/admin" data-test="rail-gov">
      <team-outlined />治理
    </router-link>
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
/* 待审批红点：贴在图标右上角，窄栏只有 64px，不能再挤占文案宽度 */
.zc-rail a[data-test="rail-admin"] { position: relative; }
.zc-rail-badge {
  position: absolute;
  top: 4px;
  right: 8px;
  min-width: 15px;
  height: 15px;
  padding: 0 4px;
  box-sizing: border-box;
  border-radius: 999px;
  background: var(--zc-danger);
  color: #fff;
  font-size: 10px;
  line-height: 15px;
  text-align: center;
}
</style>