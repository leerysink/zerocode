<script setup>
// 对话页三栏布局：左栏 SideBar + 中栏对话流/输入区（Task 13）+ 右栏文件面板（Task 14）。
// 右栏以 v-if="store.currentWorkspaceId" 为渲染门（无会话时整个面板不渲染）。
// 骨架按 brief Step 1 照抄，修正两处笔误（报告留痕）：
// ① brief 骨架多出一个 </a-layout> 闭合标签（3 闭合对 2 开启）；
// ② 外层 a-layout 需显式 has-sider——SideBar 是普通 aside 而非 a-layout-sider，antd 不会自动加 row 方向，缺省会纵向堆叠。
import { onMounted } from 'vue'
import { useChatStore } from '../stores/chat'
import SideBar from '../components/SideBar.vue'
import ChatMessages from '../components/ChatMessages.vue'
import ChatInput from '../components/ChatInput.vue'
import FilePanel from '../components/FilePanel.vue'

const store = useChatStore()
onMounted(() => store.loadWorkspaces())
</script>

<template>
  <a-layout class="zc-layout" has-sider>
    <SideBar />
    <a-layout class="zc-center">
      <ChatMessages
        :messages="store.messages"
        :streaming="store.streamingContent"
        :pending-ask="store.pendingAsk"
        :pending-confirm="store.pendingConfirm"
        :has-session="!!store.currentSessionId"
      />
      <ChatInput :running="store.running" @send="store.send" @cancel="store.cancel" />
    </a-layout>
    <FilePanel v-if="store.currentWorkspaceId" :workspace-id="store.currentWorkspaceId" />
  </a-layout>
</template>

<style scoped>
.zc-layout {
  height: 100vh;
}
.zc-center {
  min-width: 0;
  background: #fff;
}
</style>
