<script setup>
// run_python 结果卡（ADR-0056 Python 沙箱）：退出码 + 输出回执。
// 与 WebDownloadCard 同一套卡壳与颜色 token；脚本输出按文本展示，不渲染为指令。
// 002 示踪弹的最小卡：artifacts 展示由 004/007 加宽。
import { computed } from 'vue'

const props = defineProps({
  msg: { type: Object, required: true },
  progress: { type: Object, default: null },
})

const result = computed(() => props.msg.result ?? props.msg.tool_result ?? null)
const ok = computed(() => result.value?.ok === true)
const tone = computed(() => {
  if (!result.value) return 'info'
  return result.value.ok === false ? 'error' : 'ok'
})
const title = computed(() => {
  if (!result.value) return '正在执行 Python 脚本…'
  if (result.value.ok === false) return 'Python 沙箱执行失败'
  return result.value.exit_code === 0 ? 'Python 脚本已执行' : 'Python 脚本已执行（非零退出）'
})
const metaLine = computed(() => {
  const r = result.value
  if (!r || r.ok === false) return ''
  const parts = []
  if (typeof r.exit_code === 'number') parts.push(`退出码 ${r.exit_code}`)
  if (typeof r.duration_seconds === 'number') parts.push(`耗时 ${r.duration_seconds}s`)
  return parts.join(' · ')
})
const output = computed(() => (ok.value && typeof result.value?.output === 'string' ? result.value.output : ''))
const artifacts = computed(() => (ok.value && Array.isArray(result.value?.artifacts) ? result.value.artifacts : []))
const loadingLine = computed(() => props.progress?.line || '正在执行 Python 脚本…')
const toolName = computed(() => props.msg.name ?? props.msg.tool_name ?? 'run_python')
</script>

<template>
  <div class="zc-page" :class="`is-${tone}`">
    <div class="zc-page-head">
      <span class="zc-page-title">{{ title }}</span>
      <span class="zc-page-tool">{{ toolName }}</span>
    </div>

    <div v-if="!result" class="zc-page-loading">{{ loadingLine }}</div>
    <template v-else>
      <div v-if="result.ok === false" class="zc-page-error" data-test="python-error">
        {{ result.error || 'Python 沙箱执行失败' }}
      </div>
      <div v-if="metaLine" class="zc-page-meta">{{ metaLine }}</div>
      <pre v-if="output" class="zc-page-output" data-test="python-output">{{ output }}</pre>
      <div v-if="artifacts.length" class="zc-page-artifacts" data-test="python-artifacts">
        <div class="zc-page-meta">产物（文本经 write_file 落盘）：</div>
        <div v-for="a in artifacts" :key="a.name" class="zc-page-artifact">
          {{ a.name }} · {{ a.bytes }} B{{ a.content ? '' : '（二进制/超限，未回全文）' }}
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 与 WebDownloadCard / WebFetchCard 同一套卡壳与同一批颜色常量：不新增色值。 */
.zc-page {
  width: 100%;
  max-width: 720px;
  border: 1px solid var(--zc-border);
  border-left: 3px solid var(--zc-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fbfcfd;
}
.zc-page.is-ok {
  border-left-color: #52a06b;
  background: #f6fbf7;
}
.zc-page.is-error {
  border-left-color: #c4636a;
  background: #fdeeee;
}
.zc-page.is-info {
  border-left-color: var(--zc-primary);
  background: #f4f8fd;
}
.zc-page-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.zc-page-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--zc-text);
}
.zc-page-tool {
  font-size: 11px;
  color: var(--zc-text-sub);
  background: rgba(91, 122, 157, 0.08);
  border-radius: 4px;
  padding: 1px 6px;
}
.zc-page-loading,
.zc-page-meta {
  margin-top: 6px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-page-error {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: #a05a5a;
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-page-output {
  margin-top: 8px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--zc-text);
  background: rgba(91, 122, 157, 0.06);
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
}
.zc-page-artifact {
  margin-top: 4px;
  font-size: 12px;
  color: var(--zc-text);
  word-break: break-all;
}
</style>