<script setup>
// 对话页三栏布局：左栏 SideBar + 中栏对话流/输入区（Task 13）+ 任务栏 TodoBar（Task 14）+ 右栏文件面板（Task 14）。
// 左右侧栏可收起、可拖拽调宽（双击复位）；宽度与收起状态由 useChatSidePanels 记忆。
// 右栏以 v-if="store.currentWorkspaceId" 为渲染门（无会话时整栏与右缘手柄都不渲染）。
// 外层 a-layout 需显式 has-sider——SideBar 是普通 aside 而非 a-layout-sider，antd 不会自动加 row 方向，缺省会纵向堆叠。
// 被共享工作区（Ticket 007/008）进入只读模式：隐藏输入区（连同停止/压缩/切模型/附件），
// 顶部给「共享旁观 · 只读」提示；右栏隐藏记忆页签与「打开文件夹」。
// WS 仍走同一个 connect：后端按角色分流，被共享者拿到的是只读旁观连接。
// 撤销/退出（Ticket 010）：后端推 share_revoked 后以 4404 断开；此处提示并整页回应用页——
// 全量重载顺便清掉本页缓存的协作方数据（会话快照/权限态），比原地路由切换干净。
import { computed, onMounted, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useChatStore } from '../stores/chat'
import { useChatSidePanels } from '../composables/useChatSidePanels'
import SideBar from '../components/SideBar.vue'
import ChatMessages from '../components/ChatMessages.vue'
import ChatInput from '../components/ChatInput.vue'
import TodoBar from '../components/TodoBar.vue'
import FilePanel from '../components/FilePanel.vue'
import SidePanelHandle from '../components/SidePanelHandle.vue'

const store = useChatStore()
const {
  leftWidth, rightWidth, leftCollapsed, rightCollapsed, dragging,
  startDrag, resetWidth, toggleLeft, toggleRight,
} = useChatSidePanels()
onMounted(() => store.loadWorkspaces())

// 角色以 GET /workspaces 带回的 role 为准：刷新页面/直接开链接也判得出只读，不靠「从哪点进来」
const isCollaborator = computed(() => store.currentWorkspaceRole === 'collaborator')

// 提示留一小段再跳：会话此刻已不可读，早跳只会让 toast 一帧都看不清
watch(() => store.shareRevoked, (v) => {
  if (!v) return
  message.warning('共享已撤销，已退出旁观')
  setTimeout(() => window.location.assign('/apps'), 600)
})
</script>

<template>
  <a-layout class="zc-layout" has-sider>
    <SideBar :width="leftWidth" :collapsed="leftCollapsed" />
    <SidePanelHandle
      side="left"
      :edge="leftWidth"
      :collapsed="leftCollapsed"
      :dragging="dragging === 'left'"
      @dragstart="startDrag('left', $event)"
      @reset="resetWidth('left')"
      @toggle="toggleLeft"
    />
    <a-layout class="zc-center">
      <ChatMessages
        :messages="store.messages"
        :streaming="store.streamingContent"
        :streaming-thinking="store.streamingThinking"
        :has-session="!!store.currentSessionId"
        :loading="store.loadingSession"
      />
      <TodoBar v-if="store.todoList.length" :todos="store.todoList" />
      <!-- 只读旁观：不渲染输入区，就没有发送/停止/压缩/切模型/附件入口 -->
      <div v-if="isCollaborator" class="zc-readonly-bar">共享旁观 · 只读（等待所有者操作）</div>
      <!-- 发送决策在 chat store（被拦时保留输入框文本 + 给 hint），故不再冒泡 send -->
      <ChatInput v-else :running="store.running" @cancel="store.cancel" />
    </a-layout>
    <FilePanel
      v-if="store.currentWorkspaceId"
      :workspace-id="store.currentWorkspaceId"
      :width="rightWidth"
      :collapsed="rightCollapsed"
      :read-only="isCollaborator"
    />
    <SidePanelHandle
      v-if="store.currentWorkspaceId"
      side="right"
      :edge="rightWidth"
      :collapsed="rightCollapsed"
      :dragging="dragging === 'right'"
      @dragstart="startDrag('right', $event)"
      @reset="resetWidth('right')"
      @toggle="toggleRight"
    />
  </a-layout>
</template>

<style scoped>
.zc-layout {
  /* 壳层全局账户顶栏占 40px（App.vue/AccountBar），三栏占余下满高 */
  height: calc(100vh - 40px);
  /* 边缘手柄（收起钮/拖拽热区）以三栏容器为定位基准 */
  position: relative;
}
.zc-center {
  min-width: 0;
  background: var(--zc-bg-card);
}
/* 只读旁观条：代替输入区常驻底部，位置与高度接近原输入框，页面不塌 */
.zc-readonly-bar {
  padding: 14px 16px;
  border-top: 1px solid var(--zc-border);
  background: var(--zc-bg);
  color: var(--zc-text-sub);
  font-size: 13px;
  text-align: center;
}
</style>
