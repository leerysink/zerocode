<script setup>
// 委派卡（ADR-0052 / 规格 2026-11-18-agent-subruns §2.5）：delegate 调用行的一批子 run。
// 每行一个子任务：title + 状态徽标（运行中/已完成/失败/已中断）+ 回执摘要；展开看步骤留痕。
// 字段一律读归一化后的记录（msg.subruns）——实时由 reducer 归并、刷新后由子 run 行重建，
// 两种来源逐字段同形（ADR-0029 同构），组件不区分来源。
// 运行中不设折叠：步骤要跟着事件长出来，此时收起来等于什么也看不见。
import { computed } from 'vue'

const props = defineProps({
  // 归一化后的 delegate 调用记录：{ args, result?, subruns? }
  msg: { type: Object, required: true },
})

const STATUS_TEXT = { running: '运行中', completed: '已完成', error: '失败', cancelled: '已中断' }

const rows = computed(() => props.msg.subruns || [])
// 整批运行中 = 回执行还没回来（tool_result 到达即已答态）
const live = computed(() => props.msg.result === undefined || props.msg.result === null)
const failed = computed(() => {
  const r = props.msg.result
  return r && r.ok === false ? (r.error || '委派失败') : ''
})
const cancelled = computed(() => !!(props.msg.result && props.msg.result.cancelled))
const count = computed(() => rows.value.length || props.msg.args?.tasks?.length || 0)

function stateOf(row) {
  if (row.status && STATUS_TEXT[row.status]) return row.status
  return live.value ? 'running' : 'cancelled'
}

function summaryOf(row) {
  const st = stateOf(row)
  if (st === 'error') return row.error || ''
  if (st === 'completed') return row.result || ''
  return ''
}

function hasDetail(row) {
  return !!((row.texts && row.texts.length) || (row.steps && row.steps.length))
}

const pretty = (v) => JSON.stringify(v, null, 2)
</script>

<template>
  <div class="zc-delegate">
    <div class="zc-delegate-head">
      <a-spin v-if="live" size="small" class="zc-delegate-spin" />
      <span class="zc-delegate-title">委派 {{ count }} 个子任务</span>
      <span v-if="cancelled" class="zc-delegate-note">已停止</span>
      <span v-if="failed" class="zc-delegate-fail">{{ failed }}</span>
    </div>
    <div v-for="row in rows" :key="row.index" class="zc-delegate-row">
      <div class="zc-delegate-row-head">
        <span class="zc-delegate-row-title">{{ row.index + 1 }}. {{ row.title || '未命名任务' }}</span>
        <span class="zc-delegate-badge" :class="`is-${stateOf(row)}`">
          {{ STATUS_TEXT[stateOf(row)] }}
        </span>
      </div>
      <div v-if="stateOf(row) === 'running' && row.progress" class="zc-delegate-progress">
        {{ row.progress.line }}
      </div>
      <div v-if="summaryOf(row)" class="zc-delegate-summary">{{ summaryOf(row) }}</div>
      <!-- 运行中：步骤直接铺开；落定后收进折叠，默认收起、点开看留痕 -->
      <component :is="live ? 'div' : 'details'" v-if="hasDetail(row)" class="zc-delegate-detail">
        <summary v-if="!live" class="zc-delegate-detail-sum">执行留痕</summary>
        <p v-for="(t, ti) in row.texts || []" :key="`t${ti}`" class="zc-delegate-text">{{ t }}</p>
        <div v-for="(st, si) in row.steps || []" :key="`s${si}`" class="zc-delegate-step">
          <span class="zc-delegate-step-name">{{ st.name }}</span>
          <pre v-if="st.args != null" class="zc-delegate-json">{{ pretty(st.args) }}</pre>
          <pre v-if="st.result != null" class="zc-delegate-json">{{ pretty(st.result) }}</pre>
        </div>
      </component>
    </div>
  </div>
</template>

<style scoped>
.zc-delegate {
  width: 100%;
  border: 1px solid var(--zc-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}
.zc-delegate-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--zc-text-sub);
  margin-bottom: 6px;
}
.zc-delegate-title {
  font-weight: 600;
  color: var(--zc-text);
}
.zc-delegate-note {
  color: #a17c5b;
}
.zc-delegate-fail {
  color: #a05a5a;
  word-break: break-word;
}
.zc-delegate-row {
  padding: 4px 0;
}
.zc-delegate-row + .zc-delegate-row {
  border-top: 1px dashed var(--zc-border);
}
.zc-delegate-row-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.zc-delegate-row-title {
  min-width: 0;
  word-break: break-word;
  color: var(--zc-text);
}
.zc-delegate-badge {
  margin-left: auto;
  flex: none;
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 8px;
  color: var(--zc-text-sub);
  background: rgba(91, 122, 157, 0.08);
}
.zc-delegate-badge.is-running {
  color: var(--zc-primary);
  background: rgba(91, 122, 157, 0.12);
}
.zc-delegate-badge.is-completed {
  color: #4f7d4f;
  background: rgba(122, 167, 122, 0.14);
}
.zc-delegate-badge.is-error {
  color: #a05a5a;
  background: rgba(160, 90, 90, 0.1);
}
.zc-delegate-badge.is-cancelled {
  color: #a17c5b;
  background: rgba(161, 124, 91, 0.1);
}
.zc-delegate-progress {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--zc-text-sub);
  white-space: pre-wrap;
  word-break: break-all;
}
.zc-delegate-summary {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--zc-text-sub);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-delegate-detail {
  margin: 4px 0 2px;
}
.zc-delegate-detail-sum {
  cursor: pointer;
  font-size: 12px;
  color: var(--zc-text-sub);
  padding: 2px 0;
}
.zc-delegate-text {
  margin: 2px 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--zc-text-sub);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-delegate-step {
  margin: 4px 0 2px;
}
.zc-delegate-step-name {
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-delegate-json {
  margin: 3px 0 0;
  max-height: 240px;
  overflow: auto;
  background: #f6f8fa;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  padding: 6px 8px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--zc-text);
}
</style>
