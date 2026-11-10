<script setup>
// 记忆详情左栏索引：按类型分组把全部记忆列出来，标出当前看的那条、给停用的加标记。
// 只做展示与事件；取数、地址同步、「删完切相邻一条」都留在页面里（本组件不认识路由）。
import { computed } from 'vue'
import { groupMemories } from '../../utils/memory'

const props = defineProps({
  memories: { type: Array, default: () => [] },
  // 数字/字符串都可能（地址栏来的字符串、接口来的字符串），比较时统一转字符串
  activeId: { type: [Number, String], default: '' },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'retry'])

const groups = computed(() => groupMemories(props.memories))
const isActive = (m) => String(m.id) === String(props.activeId)
</script>

<template>
  <div class="zc-mil">
    <div class="zc-mil-head">
      <span>记忆</span>
      <span v-if="!loading && !failed" class="zc-mil-count">{{ memories.length }}</span>
    </div>
    <div v-if="loading" class="zc-mil-loading">读取中…</div>
    <div v-else-if="failed" class="zc-mil-error">
      <span>列表加载失败，右侧当前这条仍可查看。</span>
      <button type="button" class="zc-mil-retry" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="!memories.length" class="zc-mil-empty">暂无记忆</div>
    <div v-else class="zc-mil-list">
      <div v-for="g in groups" :key="g.type" class="zc-mil-group">
        <div class="zc-mil-group-title">{{ g.label }}（{{ g.items.length }}）</div>
        <button
          v-for="m in g.items"
          :key="m.id"
          type="button"
          class="zc-mil-item"
          :class="{ 'is-active': isActive(m) }"
          :aria-current="isActive(m) ? 'true' : undefined"
          :title="m.title || '(无标题)'"
          @click="emit('select', m)"
        >
          <span class="zc-mil-title">{{ m.title || '(无标题)' }}</span>
          <span v-if="!m.enabled" class="zc-mil-off">停用</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 宽度由组件自己定：它是整页里固定的索引列，页面只负责把它摆进左右两栏 */
.zc-mil {
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
.zc-mil-head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--zc-border);
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-mil-count {
  padding: 0 6px;
  border-radius: 8px;
  background: var(--zc-bg);
  color: var(--zc-text);
  font-size: 11px;
  line-height: 16px;
}
.zc-mil-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px;
}
.zc-mil-group + .zc-mil-group {
  margin-top: 6px;
}
.zc-mil-group-title {
  padding: 4px 6px 2px;
  font-size: 11px;
  color: var(--zc-text-sub);
}
.zc-mil-item {
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
.zc-mil-item:hover {
  background: rgba(127, 127, 127, 0.14);
}
.zc-mil-item.is-active,
.zc-mil-item.is-active:hover {
  background: #007acc;
}
.zc-mil-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.zc-mil-item.is-active .zc-mil-title {
  color: #ffffff;
}
.zc-mil-off {
  flex: none;
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(127, 127, 127, 0.16);
  color: var(--zc-text-sub);
  font-size: 10px;
  line-height: 15px;
}
.zc-mil-item.is-active .zc-mil-off {
  background: rgba(255, 255, 255, 0.22);
  color: #ffffff;
}
.zc-mil-loading,
.zc-mil-empty {
  padding: 16px 10px;
  text-align: center;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-mil-error {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 10px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-mil-retry {
  align-self: flex-start;
  border: 1px solid var(--zc-border);
  border-radius: 3px;
  background: var(--zc-bg);
  color: var(--zc-text);
  font-size: 12px;
  padding: 2px 8px;
  cursor: pointer;
}
.zc-mil-retry:hover {
  border-color: #007acc;
}
</style>
