<script setup>
// 顶部任务栏：todo_sync 驱动的实时清单，可折叠；纯展示（状态由 agent 维护）
import { computed, ref } from 'vue'
import { DownOutlined, RightOutlined } from '@ant-design/icons-vue'

const props = defineProps({ todos: { type: Array, default: () => [] } })
const open = ref(true)
const doneCount = computed(() => props.todos.filter((t) => t.status === 'completed').length)
const STATUS_TEXT = { pending: '待办', in_progress: '进行中', completed: '已完成' }
</script>

<template>
  <div class="zc-todo-bar">
    <button class="zc-todo-head" type="button" @click="open = !open">
      <component :is="open ? DownOutlined : RightOutlined" class="zc-todo-arrow" />
      <span>任务清单</span>
      <span class="zc-todo-count">{{ doneCount }}/{{ todos.length }} 已完成</span>
    </button>
    <ul v-show="open" class="zc-todo-list">
      <li v-for="(t, i) in todos" :key="i" :class="`is-${t.status}`">
        <span class="zc-todo-dot" />
        <span class="zc-todo-text">{{ t.content }}</span>
        <span class="zc-todo-status">{{ STATUS_TEXT[t.status] || t.status }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.zc-todo-bar {
  flex: 0 0 auto;
  margin: 0 28px;
  border: 1px solid var(--zc-border);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}
.zc-todo-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: rgba(91, 122, 157, 0.05);
  padding: 6px 12px;
  font-size: 12.5px;
  color: var(--zc-text);
  cursor: pointer;
}
.zc-todo-arrow { font-size: 10px; color: var(--zc-text-sub); }
.zc-todo-count { color: var(--zc-text-sub); }
.zc-todo-list { list-style: none; margin: 0; padding: 6px 12px 8px; }
.zc-todo-list li { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 13px; }
.zc-todo-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--zc-border); flex: 0 0 auto; }
.is-in_progress .zc-todo-dot { background: var(--zc-primary); }
.is-completed .zc-todo-dot { background: #7aa77a; }
.is-completed .zc-todo-text { color: var(--zc-text-sub); text-decoration: line-through; }
.zc-todo-status { margin-left: auto; font-size: 11px; color: var(--zc-text-sub); }
</style>
