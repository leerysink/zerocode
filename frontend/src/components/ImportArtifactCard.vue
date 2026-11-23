<script setup>
// 产物导入回执卡（ADR-0066 决策 7）：落盘路径 + 体积 + 不可回滚提示。
// 与 RunPythonCard / WebDownloadCard 同一套卡壳与同一批颜色常量，不新增色值。
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
  if (!result.value) return '正在导入产物…'
  return result.value.ok === false ? '产物导入失败' : '产物已落盘'
})
const landedPath = computed(() => result.value?.path || props.msg.args?.path || '')
const artifactName = computed(() => props.msg.args?.name || '')
const sizeText = computed(() => {
  const b = result.value?.bytes
  if (typeof b !== 'number') return ''
  return b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${(b / 1024).toFixed(1)} KB`
})
const notice = computed(() => (ok.value ? (result.value?.notice || '') : ''))
const loadingLine = computed(() => props.progress?.line || '正在导入产物…')
</script>

<template>
  <div class="zc-page" :class="`is-${tone}`">
    <div class="zc-page-head">
      <span class="zc-page-title">{{ title }}</span>
      <span class="zc-page-tool">import_artifact</span>
    </div>

    <div v-if="!result" class="zc-page-loading">{{ loadingLine }}</div>
    <template v-else>
      <div v-if="result.ok === false" class="zc-page-error" data-test="import-error">
        {{ result.error || '产物导入失败' }}
      </div>
    </template>
    <div v-if="landedPath" class="zc-page-meta" data-test="import-path">
      {{ artifactName ? `${artifactName} → ` : '' }}{{ landedPath }}<span v-if="sizeText"> · {{ sizeText }}</span>
    </div>
    <div v-if="notice" class="zc-page-notice" data-test="import-notice">{{ notice }}</div>
  </div>
</template>

<style scoped>
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
  word-break: break-all;
}
.zc-page-notice {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
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
</style>