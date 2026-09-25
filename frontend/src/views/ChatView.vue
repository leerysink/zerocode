<script setup>
// 对话页三栏布局：左栏 SideBar + 中栏对话流/输入区（Task 13）+ 任务栏 TodoBar（Task 14）+ 右栏文件面板（Task 14）。
// 右栏以 v-if="store.currentWorkspaceId" 为渲染门（无会话时整个面板不渲染）。
// 骨架按 brief Step 1 照抄，修正两处笔误（报告留痕）：
// ① brief 骨架多出一个 </a-layout> 闭合标签（3 闭合对 2 开启）；
// ② 外层 a-layout 需显式 has-sider——SideBar 是普通 aside 而非 a-layout-sider，antd 不会自动加 row 方向，缺省会纵向堆叠。
import { onMounted } from 'vue'
import { useChatStore } from '../stores/chat'
import SideBar from '../components/SideBar.vue'
import AccountBar from '../components/AccountBar.vue'
import ChatMessages from '../components/ChatMessages.vue'
import ChatInput from '../components/ChatInput.vue'
import TodoBar from '../components/TodoBar.vue'
import FilePanel from '../components/FilePanel.vue'

const store = useChatStore()
onMounted(() => store.loadWorkspaces())
</script>

<template>
  <a-layout class="zc-layout" has-sider>
    <SideBar />
    <!-- 普通 div 而非 a-layout：外层 has-sider 会把 a-layout 子项一律按列排，账户顶栏要横跨中栏+右栏上方 -->
    <div class="zc-right">
      <AccountBar />
      <a-layout class="zc-body" has-sider>
        <a-layout class="zc-center">
          <ChatMessages
            :messages="store.messages"
            :streaming="store.streamingContent"
            :streaming-thinking="store.streamingThinking"
            :pending-ask="store.pendingAsk"
            :pending-confirm="store.pendingConfirm"
            :has-session="!!store.currentSessionId"
          />
          <TodoBar v-if="store.todoList.length" :todos="store.todoList" />
          <!-- 发送决策在 chat store（被拦时保留输入框文本 + 给 hint），故不再冒泡 send -->
          <ChatInput :running="store.running" @cancel="store.cancel" />
        </a-layout>
        <FilePanel v-if="store.currentWorkspaceId" :workspace-id="store.currentWorkspaceId" />
      </a-layout>
    </div>
  </a-layout>
</template>

<style scoped>
.zc-layout {
  height: 100vh;
}
.zc-right {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.zc-body {
  flex: 1;
  min-height: 0;
}
.zc-center {
  min-width: 0;
  background: #fff;
}
</style>
