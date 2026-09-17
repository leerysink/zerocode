<script setup>
// 对话流内任务卡：todo_write 调用处渲染当次清单快照
defineProps({ todos: { type: Array, default: () => [] } })
const STATUS_TEXT = { pending: '待办', in_progress: '进行中', completed: '已完成' }
</script>

<template>
  <div class="zc-todo-card">
    <div class="zc-todo-card-head">任务清单更新</div>
    <ul>
      <li v-for="(t, i) in todos" :key="i" :class="`is-${t.status}`">
        <span class="zc-dot" /><span>{{ t.content }}</span>
        <span class="zc-st">{{ STATUS_TEXT[t.status] || t.status }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.zc-todo-card {
  width: 100%;
  max-width: 640px;
  border: 1px solid var(--zc-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}
.zc-todo-card-head { font-size: 12px; color: var(--zc-text-sub); margin-bottom: 6px; }
.zc-todo-card ul { list-style: none; margin: 0; padding: 0; }
.zc-todo-card li { display: flex; align-items: center; gap: 8px; padding: 2px 0; font-size: 13px; }
.zc-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--zc-border); }
.is-in_progress .zc-dot { background: var(--zc-primary); }
.is-completed .zc-dot { background: #7aa77a; }
.is-completed span:nth-child(2) { color: var(--zc-text-sub); text-decoration: line-through; }
.zc-st { margin-left: auto; font-size: 11px; color: var(--zc-text-sub); }
</style>
