<script setup>
// diff 渲染块：unified diff 逐行渲染（add 绿底 / del 红底 / hunk 灰字，等宽字体）。
// Task 11/12 预警：parseDiff 会把头两行 `--- a/x` / `+++ b/x` 误归为 del/add——
// 此处在渲染侧过滤这两类头行（仅限前两行），不改 parseDiff 本身（决策留痕见 task-13-report）。
// 「查看具体」：侧栏那么宽放不下两侧全文，所以整页在新标签里看（地址收口在 utils/openFolder）。
// 只有拿得到记录 id 才出现这个入口——聊天流里存量历史挂的 diff 没有 id，不给一个点了没反应的按钮。
import { computed } from 'vue'
import { message } from 'ant-design-vue'
import { parseDiff } from '../utils/diff'
import { fileChangePath, openFileChangePage } from '../utils/openFolder'

const props = defineProps({
  diff: { type: String, default: '' },
  path: { type: String, default: '' },
  action: { type: String, default: '' },
  // 会话与记录 id：两个都齐了才拼得出详情整页地址（详情接口挂在会话下，归属校验走会话链）
  sessionId: { type: [Number, String], default: '' },
  changeId: { type: [Number, String], default: '' },
})

// 与后端 tools.py 的 action 取值对应（create/edit/delete），未知值原样展示
const ACTION_LABEL = { create: '新建', edit: '修改', delete: '删除' }

// 能不能看详情，判据就是「地址拼不拼得出来」——不另立一套 id 合法性判定，
// 免得两处规则漂移（openFolder 那边收紧成只认纯数字时，这里自动跟着收）
const detailUrl = computed(() => fileChangePath(props.sessionId, props.changeId))

function openDetail() {
  if (!openFileChangePage(props.sessionId, props.changeId)) {
    message.error('浏览器拦住了新标签，允许弹窗后再点一次')
  }
}

// 头行过滤仅作用于前两行（行索引 <2 且形如 `--- `/`+++ ` 开头才滤）：
// 后端 unified diff 的文件头必在最前两行；正文里原文内容为 `-- x`/`++ y` 的 del/add 行
// （raw 为 `--- x`/`+++ y`）不再被误伤。残余限制：无头行 diff 的首两行内容若恰形如 `--- ` 仍会被滤
// （显示层缓解的取舍，见 task-13-report 修复记录）。
const lines = computed(() =>
  parseDiff(props.diff).filter(
    (l, idx) => idx >= 2 || !(l.text.startsWith('--- ') || l.text.startsWith('+++ '))
  )
)
</script>

<template>
  <div class="zc-diff">
    <div class="zc-diff-head">
      <span v-if="action" class="zc-diff-action">{{ ACTION_LABEL[action] || action }}</span>
      <span class="zc-diff-path">{{ path || '文件变更' }}</span>
      <button
        v-if="detailUrl"
        type="button"
        class="zc-diff-open"
        :title="`在新标签打开这条变更的两侧对比`"
        @click="openDetail"
      >查看具体</button>
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
/* 贴到条目右缘：路径名长短不一，按钮跟着走会让整列变更记录看起来在抖 */
.zc-diff-open {
  flex-shrink: 0;
  margin-left: auto;
  border: 1px solid var(--zc-border);
  border-radius: 4px;
  background: #fff;
  color: var(--zc-text-sub);
  font-size: 11px;
  line-height: 16px;
  padding: 0 6px;
  cursor: pointer;
}
.zc-diff-open:hover {
  color: var(--zc-primary);
  border-color: var(--zc-primary);
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
