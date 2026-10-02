<script setup>
// 内置文件管理器整页（新标签打开 /workspaces/:id/files）。
// 为什么是整页不是弹框：文件浏览是一件要长时间占用视线的事，压在聊天页上会让两边都只能用一半宽度；
// 独立地址还能收藏、能后退、能开在第二个屏幕上对照着写代码。
// 本页只管「地址 → 该不该挂文件管理器」这一件事：树与预览的行为都在 WorkspaceExplorer 里。
// id 全程按字符串透传（不转 Number）；非法地址一个目录请求都不发——后端按归属校验会给 404，
// 但那要等请求回来才知道，这里先挡住，页面也不会挂出一个空壳。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import WorkspaceExplorer from '../components/WorkspaceExplorer.vue'
import { normalizeWorkspaceId } from '../utils/openFolder'

const route = useRoute()

const workspaceId = computed(() => normalizeWorkspaceId(route.params.id))

// 名字由入口带在 query 上（入口本来就知道名字，不为一个标题再请求一次工作区）。
// 手改地址可能给出 ?name=a&name=b，vue-router 会解析成数组：取第一个，不能让它渲染成「a,b」。
const workspaceName = computed(() => {
  const raw = Array.isArray(route.query.name) ? route.query.name[0] : route.query.name
  return String(raw ?? '')
})

const heading = computed(() => {
  const label = workspaceName.value || (workspaceId.value ? `#${workspaceId.value}` : '未知工作区')
  return `工作区文件 · ${label}`
})
</script>

<template>
  <div class="zc-wsv">
    <div class="zc-wsv-head">
      <h2 :title="heading">{{ heading }}</h2>
      <span class="zc-wsv-tip">只读浏览与下载，编辑请回聊天页的文件面板</span>
    </div>

    <a-alert
      v-if="!workspaceId"
      class="zc-wsv-bad"
      type="error"
      show-icon
      message="工作区地址不正确"
      description="地址里的 /workspaces/后面的数字不是你的工作区，请从应用卡片或聊天页右栏重新打开。"
    />
    <div v-else class="zc-wsv-body">
      <WorkspaceExplorer :workspace-id="workspaceId" :workspace-name="workspaceName" />
    </div>
  </div>
</template>

<style scoped>
/* 壳层账户栏占 40px（App.vue/AccountBar），沿用 DB 操作台的整页高度口径 */
.zc-wsv {
  box-sizing: border-box;
  height: calc(100vh - 40px);
  display: flex;
  flex-direction: column;
  padding: 0 16px 16px;
  overflow: hidden;
}
.zc-wsv-head {
  flex: 0 0 auto;
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--zc-border);
}
.zc-wsv-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  min-width: 0;
  max-width: 70%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.zc-wsv-tip {
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-wsv-bad {
  margin-top: 16px;
}
.zc-wsv-body {
  flex: 1;
  min-height: 0;
}
</style>
