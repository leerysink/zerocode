<script setup>
// 工具调用块：role=tool 消息折叠展示——头部 `🔧 工具名`，展开看 JSON 参数/结果。
// 实时消息字段为 name/args/result，历史消息为 tool_name/tool_args/tool_result，二者都兼容。
// 写入类工具的 file_change 事件挂在最近的 tool 消息上（store.applyEvent），有 diff 时在其下渲染 DiffBlock。
import { computed } from 'vue'
import DiffBlock from './DiffBlock.vue'

const props = defineProps({ msg: { type: Object, required: true } })

const toolName = computed(() => props.msg.name ?? props.msg.tool_name ?? 'tool')
const args = computed(() => props.msg.args ?? props.msg.tool_args ?? null)
const result = computed(() => props.msg.result ?? props.msg.tool_result ?? null)

const pretty = (v) => JSON.stringify(v, null, 2)
</script>

<template>
  <div class="zc-toolblock">
    <a-collapse ghost>
      <a-collapse-panel key="tool" :header="`🔧 ${toolName}`">
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
