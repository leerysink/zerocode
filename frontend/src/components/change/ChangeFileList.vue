<script setup>
// 变更详情左栏的文件索引：把会话里的全部变更按路径列出来，标出当前看的那条。
// 只做展示与事件，取数、地址同步、「点当前条不重复请求」都留在页面里（ChangeFileList 不认识路由）。
import { computed } from 'vue'

const props = defineProps({
  changes: { type: Array, default: () => [] },
  // 数字/字符串都可能（地址栏来的字符串、接口来的字符串），比较时统一转字符串
  activeId: { type: [Number, String], default: '' },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'retry'])

// 与后端 tools.py 的 action 取值对应（create/edit/delete），未知值原样展示
const ACTION_LABEL = { create: '新建', edit: '修改', delete: '删除' }
const labelOf = (a) => ACTION_LABEL[a] || a || '变更'

const isActive = (c) => String(c.id) === String(props.activeId)

const countText = computed(() => String(props.changes.length))
</script>

<template>
  <div class="zc-cfl">
    <div class="zc-cfl-head">
      <span>变更文件</span>
      <span v-if="!loading && !failed" class="zc-cfl-count">{{ countText }}</span>
    </div>
    <div v-if="loading" class="zc-cfl-loading">读取中…</div>
    <div v-else-if="failed" class="zc-cfl-error">
      <span>列表加载失败，右侧当前这条仍可查看。</span>
      <button type="button" class="zc-cfl-retry" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="!changes.length" class="zc-cfl-empty">暂无变更</div>
    <div v-else class="zc-cfl-list">
      <button
        v-for="c in changes"
        :key="c.id"
        type="button"
        class="zc-cfl-item"
        :class="{ 'is-active': isActive(c) }"
        :aria-current="isActive(c) ? 'true' : undefined"
        :title="c.path"
        @click="emit('select', c)"
      >
        <span class="zc-cfl-action" :class="'is-' + (c.action || 'unknown')">{{ labelOf(c.action) }}</span>
        <span class="zc-cfl-path">{{ c.path || '（未命名文件）' }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
/* 宽度由组件自己定：它是整页里固定的索引列，页面只负责把它摆进左右两栏 */
.zc-cfl {
  flex: 0 0 260px;
  width: 260px;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  background: var(--zc-sidebar-bg);
  overflow: hidden;
}
.zc-cfl-head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--zc-border);
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-cfl-count {
  padding: 0 6px;
  border-radius: 8px;
  background: var(--zc-bg);
  color: var(--zc-text);
  font-size: 11px;
  line-height: 16px;
}
.zc-cfl-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px;
}
.zc-cfl-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 6px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--zc-text);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.zc-cfl-item:hover {
  background: rgba(127, 127, 127, 0.14);
}
.zc-cfl-item.is-active,
.zc-cfl-item.is-active:hover {
  background: #007acc;
}
.zc-cfl-item.is-active .zc-cfl-path {
  color: #ffffff;
}
.zc-cfl-action {
  flex: none;
  padding: 0 5px;
  border-radius: 3px;
  font-size: 11px;
  line-height: 16px;
}
.zc-cfl-action.is-create {
  background: rgba(64, 158, 255, 0.18);
  color: #4a9eff;
}
.zc-cfl-action.is-edit {
  background: rgba(230, 162, 60, 0.18);
  color: #d99a2b;
}
.zc-cfl-action.is-delete {
  background: rgba(245, 108, 108, 0.18);
  color: #e06c6c;
}
.zc-cfl-item.is-active .zc-cfl-action {
  background: rgba(255, 255, 255, 0.22);
  color: #ffffff;
}
.zc-cfl-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--zc-text);
}
.zc-cfl-loading,
.zc-cfl-empty {
  padding: 16px 10px;
  text-align: center;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-cfl-error {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 10px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-cfl-retry {
  align-self: flex-start;
  border: 1px solid var(--zc-border);
  border-radius: 3px;
  background: var(--zc-bg);
  color: var(--zc-text);
  font-size: 12px;
  padding: 2px 8px;
  cursor: pointer;
}
.zc-cfl-retry:hover {
  border-color: #007acc;
}
</style>
