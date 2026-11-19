<script setup>
// 加载态与空态合一：调用点只说「在不在加载 + 空的时候讲什么」，不再各页手写一套
// a-spin 外置 + a-empty 内置的节奏（ADR-0062 §1）。
defineProps({
  loading: { type: Boolean, default: false },
  description: { type: String, default: '暂无数据' },
})
</script>

<template>
  <a-spin v-if="loading" class="zc-empty-spin" data-test="empty-loading" />
  <a-empty v-else class="zc-empty" :description="description" data-test="empty-state" />
</template>

<style scoped>
.zc-empty-spin { display: block; margin: 60px auto; }
.zc-empty { margin: 80px auto; }
</style>
