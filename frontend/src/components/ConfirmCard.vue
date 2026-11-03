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
  // 临时进度（tool_progress）：只有 preview_restart 会发，且只在该工具**已受理开跑**后才有，
  // 所以它同时是「后端真的收到了我的点击」的证据——见下方 acked。
  progress: { type: Object, default: null },
})
const store = useChatStore()

const COPY = {
  write_file: { pending: '写入确认', ok: '确认写入', no: '拒绝', done: '已批准写入', acked: '已受理' },
  edit_file: { pending: '修改确认', ok: '确认写入', no: '拒绝', done: '已批准修改', acked: '已受理' },
  db_write: { pending: '数据库写入确认', ok: '确认写入', no: '拒绝', done: '已批准写库', acked: '已受理' },
  preview_restart: { pending: '重启预览', ok: '确认重启', no: '取消', done: '已确认重启', acked: '已受理，正在重启' },
}
const FALLBACK = { pending: '操作确认', ok: '确认', no: '拒绝', done: '已批准', acked: '已受理' }
const DECIDED_HEAD = { denied: '已拒绝', cancelled: '已中断' }

const copy = computed(() => COPY[props.msg.name] || FALLBACK)
const pending = computed(() => props.state === 'pending')
// 已受理：待答标记还在（结果行没回来），但工具已经在跑了。
// preview_restart 会把 stop+start+健康探测整段跑完才落结果行（默认最多等 240s），
// 没有这一格的话，点完「确认重启」到结果到达之间界面一格都不动，用户只能读成「没点上」。
const acked = computed(() => pending.value && !!props.progress)
const okLabel = computed(() => (acked.value ? copy.value.acked : copy.value.ok))
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
    <div v-if="acked" class="zc-confirm-progress">{{ progress.line }}</div>
    <div v-if="pending" class="zc-confirm-actions">
      <a-button type="primary" :disabled="acked" :loading="acked" @click="store.confirm(true)">{{ okLabel }}</a-button>
      <a-button :disabled="acked" @click="store.confirm(false)">{{ copy.no }}</a-button>
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
.zc-confirm-progress {
  margin-top: 8px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--zc-text-sub);
  white-space: pre-wrap;
  word-break: break-all;
}
.zc-confirm-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
</style>
