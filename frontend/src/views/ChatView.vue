<script setup>
// 对话页三栏布局：左栏 SideBar + 中栏对话流/输入区（Task 13）+ 任务栏 TodoBar（Task 14）+ 右栏文件面板（Task 14）。
// 左右侧栏可收起、可拖拽调宽（双击复位）；宽度与收起状态由 useChatSidePanels 记忆。
// 右栏以 v-if="store.currentWorkspaceId" 为渲染门（无会话时整栏与右缘手柄都不渲染）。
// 外层 a-layout 需显式 has-sider——SideBar 是普通 aside 而非 a-layout-sider，antd 不会自动加 row 方向，缺省会纵向堆叠。
import { onMounted } from 'vue'
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
      />
      <TodoBar v-if="store.todoList.length" :todos="store.todoList" />
      <!-- 发送决策在 chat store（被拦时保留输入框文本 + 给 hint），故不再冒泡 send -->
      <ChatInput :running="store.running" @cancel="store.cancel" />
    </a-layout>
    <FilePanel
      v-if="store.currentWorkspaceId"
      :workspace-id="store.currentWorkspaceId"
      :width="rightWidth"
      :collapsed="rightCollapsed"
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
  background: #fff;
}
</style>
