<script setup>
// 工具调用块：role=tool 消息折叠展示——头部 `🔧 工具名`，展开看 JSON 参数/结果。
// msg 一律是归一化后的记录（name/args/result），实时与历史同形，见 stores/messageModel.js。
// 写入类工具的 file_change 事件挂在最近的 tool 消息上（store.applyEvent），有 diff 时在其下渲染 DiffBlock。
import { computed } from 'vue'
import DiffBlock from './DiffBlock.vue'

const props = defineProps({
  msg: { type: Object, required: true },
  // 会话 id 由宿主传进来（本组件不碰 store：它现在只吃一条归一化后的消息，好测也好复用）
  sessionId: { type: [Number, String], default: '' },
})

const toolName = computed(() => props.msg.name || 'tool')
const args = computed(() => props.msg.args ?? null)
const result = computed(() => props.msg.result ?? null)

// clarify_done 的「我对需求的理解是…」是用户对这次自声明豁免唯一看得见的东西（ADR-0032），
// 所以它进标题、而不是只躺在折叠面板的 JSON 里。文案一律读消息行的字段——实时与刷新后同形
// 才有 ADR-0029 那条不变式，事件只提供标记不提供数据源。
const header = computed(() => {
  const name = toolName.value
  if (name !== 'clarify_done') return `🔧 ${name}`
  const r = result.value
  if (!r) return `🔧 ${name}`
  // 失败要显式说出来：收口没过却画成普通工具行，用户看不出这一轮其实没对齐
  if (r.ok === false) return `⚠ 澄清收口未通过：${r.error || '缺少 understanding'}`
  if (!r.understanding) return `🔧 ${name}`
  const skipped = r.basis === 'already_clear' && !r.asked
  return `✓ 需求已对齐：${r.understanding}${skipped ? '（未提问，直接开工）' : ''}`
})

const pretty = (v) => JSON.stringify(v, null, 2)
</script>

<template>
  <div class="zc-toolblock">
    <a-collapse ghost>
      <a-collapse-panel key="tool" :header="header">
        <template v-if="args != null">
          <div class="zc-tool-label">参数</div>
          <pre class="zc-tool-json">{{ pretty(args) }}</pre>
        </template>
        <template v-if="result != null">
          <div class="zc-tool-label">结果</div>
          <pre class="zc-tool-json">{{ pretty(result) }}</pre>
        </template>
        <DiffBlock
          v-if="msg.diff"
          :diff="msg.diff"
          :path="msg.diff_path || ''"
          :action="msg.diff_action || ''"
          :session-id="sessionId"
          :change-id="msg.diff_change_id ?? ''"
        />
      </a-collapse-panel>
    </a-collapse>
  </div>
</template>

<style scoped>
.zc-toolblock {
  width: 100%;
  min-width: 0;
}
.zc-toolblock :deep(.ant-collapse-header) {
  padding: 3px 0 !important;
  color: var(--zc-text-sub);
  font-size: 13px;
}
.zc-toolblock :deep(.ant-collapse-content-box) {
  padding: 0 0 6px 22px !important;
}
.zc-tool-label {
  margin: 6px 0 4px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-tool-json {
  margin: 0;
  max-height: 240px;
  overflow: auto;
  background: #f6f8fa;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--zc-text);
}
</style>
