<script setup>
// 确认卡：写入/改文件、写库、重启预览的放行闸。与提问卡同一套状态机——
// 待答态由 confirm_request 事件打在调用行上的标记驱动，已答态由结果行里的 confirm 字段驱动，
// 所以刷新后两态都能重建（已答态靠行，待答态靠 hub 重连补发的同一个标记）。
//
// 人话文案按工具名在前端生成，不依赖后端事件里的 summary：summary 不落消息行，
// 用它渲染已答态就会让实时与刷新后叉开。待答态可以用它——重连补发会把 summary 一并带回来。
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'

const props = defineProps({
  msg: { type: Object, required: true },
  state: { type: String, required: true },
})
const store = useChatStore()

const COPY = {
  write_file: { pending: '写入确认', ok: '确认写入', no: '拒绝', done: '已批准写入' },
  edit_file: { pending: '修改确认', ok: '确认写入', no: '拒绝', done: '已批准修改' },
  db_write: { pending: '数据库写入确认', ok: '确认写入', no: '拒绝', done: '已批准写库' },
  preview_restart: { pending: '重启预览', ok: '确认重启', no: '取消', done: '已确认重启' },
}
const FALLBACK = { pending: '操作确认', ok: '确认', no: '拒绝', done: '已批准' }
const DECIDED_HEAD = { denied: '已拒绝', cancelled: '已中断' }

const copy = computed(() => COPY[props.msg.name] || FALLBACK)
const pending = computed(() => props.state === 'pending')
const headText = computed(() => {
  if (!pending.value) return DECIDED_HEAD[props.state] || copy.value.done
  return copy.value.pending
})

// 参数摘要：超长截断，避免大文件内容撑爆卡片
const argsSummary = computed(() => {
  const args = props.msg.args
  if (args == null) return ''
  const text = JSON.stringify(args, null, 2)
  return text.length > 600 ? `${text.slice(0, 600)}\n…（已截断）` : text
})

// summary 只服务待答态；已答态一律回落参数摘要，保证与刷新后逐字一致
const bodyText = computed(() => (pending.value ? props.msg.confirmSummary || '' : ''))
</script>

<template>
  <div class="zc-confirm" :class="{ 'is-decided': !pending }">
    <div class="zc-confirm-head">
      {{ headText }}<span class="zc-confirm-tool">{{ msg.name }}</span>
    </div>
    <p v-if="bodyText" class="zc-confirm-summary">{{ bodyText }}</p>
    <pre v-else-if="argsSummary" class="zc-confirm-args">{{ argsSummary }}</pre>
    <div v-if="pending" class="zc-confirm-actions">
      <a-button type="primary" @click="store.confirm(true)">{{ copy.ok }}</a-button>
      <a-button @click="store.confirm(false)">{{ copy.no }}</a-button>
    </div>
  </div>
</template>

<style scoped>
.zc-confirm {
  width: 100%;
  max-width: 640px;
  background: #fdfaf3;
  border: 1px solid #ecdfc4;
  border-radius: 8px;
  padding: 12px 14px;
}
/* 已答态：留痕而非待办，配色退到中性灰，不再用待确认的暖黄 */
.zc-confirm.is-decided {
  background: #fafbfc;
  border-color: var(--zc-border);
}
.zc-confirm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  color: #8a6d3b;
}
.zc-confirm.is-decided .zc-confirm-head {
  color: var(--zc-text-sub);
}
.zc-confirm-tool {
  font-size: 12px;
  font-weight: 400;
  background: rgba(138, 109, 59, 0.1);
  border-radius: 4px;
  padding: 1px 8px;
}
.zc-confirm.is-decided .zc-confirm-tool {
  background: rgba(47, 58, 71, 0.06);
}
.zc-confirm-args {
  margin: 10px 0 0;
  max-height: 200px;
  overflow: auto;
  background: #fff;
  border: 1px solid #ecdfc4;
  border-radius: 6px;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--zc-text);
}
.zc-confirm.is-decided .zc-confirm-args {
  border-color: var(--zc-border);
  max-height: 120px;
}
.zc-confirm-summary {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--zc-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-confirm-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
</style>
