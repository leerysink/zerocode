<script setup>
// diff 渲染块：unified diff 逐行渲染（add 绿底 / del 红底 / hunk 灰字，等宽字体）。
// Task 11/12 预警：parseDiff 会把头两行 `--- a/x` / `+++ b/x` 误归为 del/add——
// 此处在渲染侧过滤这两类头行，不改 parseDiff 本身（决策留痕见 task-13-report）。
import { computed } from 'vue'
import { parseDiff } from '../utils/diff'

const props = defineProps({
  diff: { type: String, default: '' },
  path: { type: String, default: '' },
  action: { type: String, default: '' },
})

// 与后端 tools.py 的 action 取值对应（create/edit/delete），未知值原样展示
const ACTION_LABEL = { create: '新建', edit: '修改', delete: '删除' }

// 统一 diff 中 `--- `/`+++ ` 只出现在文件头；正文里的此类内容必带 +/- 前缀（如 `- --- x`），不受影响
const lines = computed(() =>
  parseDiff(props.diff).filter((l) => !l.text.startsWith('--- ') && !l.text.startsWith('+++ '))
)
</script>

<template>
  <div class="zc-diff">
    <div class="zc-diff-head">
      <span v-if="action" class="zc-diff-action">{{ ACTION_LABEL[action] || action }}</span>
      <span class="zc-diff-path">{{ path || '文件变更' }}</span>
    </div>
    <pre class="zc-diff-body"><code><span
      v-for="(l, i) in lines"
      :key="i"
      class="zc-diff-line"
      :class="'is-' + l.type"
    >{{ l.text || ' ' }}</span></code></pre>
  </div>
</template>

<style scoped>
.zc-diff {
  margin-top: 8px;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}
.zc-diff-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  background: var(--zc-sidebar-bg);
  border-bottom: 1px solid var(--zc-border);
  font-size: 12px;
}
.zc-diff-action {
  flex-shrink: 0;
  color: var(--zc-primary);
  background: rgba(91, 122, 157, 0.1);
  border-radius: 4px;
  padding: 0 6px;
  line-height: 18px;
}
.zc-diff-path {
  color: var(--zc-text-sub);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.zc-diff-body {
  margin: 0;
  padding: 6px 0;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  overflow-x: auto;
  white-space: pre;
}
.zc-diff-line {
  display: block;
  padding: 0 10px;
  color: var(--zc-text);
}
.zc-diff-line.is-add {
  background: #edf6ed;
  color: #47705a;
}
.zc-diff-line.is-del {
  background: #fbf0f0;
  color: #a05a5a;
}
.zc-diff-line.is-hunk {
  color: var(--zc-text-sub);
  background: var(--zc-bg);
}
</style>
