<script setup>
// 对话页布局：左栏 SideBar（工作区/会话）+ 对话区（后续任务实现）+ 右栏占位（后续任务实现）
import SideBar from '../components/SideBar.vue'
</script>

<template>
  <div class="zc-layout">
    <SideBar />
    <main class="zc-main">
      <!-- 对话区占位：后续任务实现 -->
      <div class="zc-main-empty">对话</div>
    </main>
    <!-- 右栏占位：后续任务填充 -->
    <div class="zc-right"></div>
  </div>
</template>

<style scoped>
.zc-layout {
  display: flex;
  height: 100vh;
}
.zc-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--zc-text-sub);
}
.zc-right {
  flex: 0 0 auto;
}
</style>
