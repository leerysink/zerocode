<script setup>
// web_download 结果卡（ADR-0054 资源下载）：url → path 的落盘回执 + 抓取元信息。
// 与 WebFetchCard 同一套卡壳与颜色 token；资源字节不进聊天，只画回执，不渲染内容。
import { computed } from 'vue'

const props = defineProps({
  msg: { type: Object, required: true },
  progress: { type: Object, default: null },
})

const result = computed(() => props.msg.result ?? props.msg.tool_result ?? null)
const argsUrl = computed(() => (typeof props.msg.args?.url === 'string' ? props.msg.args.url : ''))
const argsPath = computed(() => (typeof props.msg.args?.path === 'string' ? props.msg.args.path : ''))
const ok = computed(() => result.value?.ok === true)
const tone = computed(() => {
  if (!result.value) return 'info'
  return result.value.ok === false ? 'error' : 'ok'
})
const title = computed(() => {
  if (!result.value) return '正在下载资源…'
  if (result.value.ok === false) return '资源下载失败'
  return result.value.existed ? '资源已下载（覆盖已有文件）' : '资源已下载'
})
const url = computed(() => (ok.value ? result.value?.url || argsUrl.value : argsUrl.value))
const path = computed(() => (ok.value ? result.value?.path || argsPath.value : argsPath.value))
const metaLine = computed(() => {
  const r = result.value
  if (!ok.value) return ''
  const parts = []
  if (r.status) parts.push(`HTTP ${r.status}`)
  if (r.content_type) parts.push(r.content_type)
  if (typeof r.bytes === 'number') parts.push(`${Math.max(1, Math.round(r.bytes / 1024))} KB`)
  const redirects = (r.redirects || []).length
  if (redirects) parts.push(`跳转 ${redirects} 次`)
  if (r.elapsed_ms) parts.push(`耗时 ${r.elapsed_ms}ms`)
  return parts.join(' · ')
})
const loadingLine = computed(() => props.progress?.line || '正在下载资源…')
const toolName = computed(() => props.msg.name ?? props.msg.tool_name ?? 'web_download')
</script>

<template>
  <div class="zc-page" :class="`is-${tone}`">
    <div class="zc-page-head">
      <span class="zc-page-title">{{ title }}</span>
      <span class="zc-page-tool">{{ toolName }}</span>
      <a v-if="url" class="zc-page-link" :href="url" target="_blank" rel="noopener noreferrer">
        打开原资源
      </a>
    </div>

    <div v-if="!result" class="zc-page-loading">{{ loadingLine }}</div>
    <template v-else>
      <div v-if="result.ok === false" class="zc-page-error" data-test="download-error">
        {{ result.error || '资源下载失败' }}
      </div>
      <div v-if="url" class="zc-page-meta zc-page-url">{{ url }}</div>
      <div v-if="path" class="zc-page-path" data-test="download-path">→ {{ path }}</div>
      <div v-if="metaLine" class="zc-page-meta">{{ metaLine }}</div>
      <div v-if="ok && result.existed" class="zc-page-note">已覆盖同名文件，此次下载不可回滚</div>
    </template>
  </div>
</template>

<style scoped>
/* 与 WebFetchCard / PageInspectCard 同一套卡壳与同一批颜色常量：不新增色值。 */
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
.zc-page-link {
  margin-left: auto;
  font-size: 12px;
  color: var(--zc-primary);
  white-space: nowrap;
}
.zc-page-loading,
.zc-page-note,
.zc-page-meta {
  margin-top: 6px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-page-url {
  word-break: break-all;
}
.zc-page-path {
  margin-top: 4px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--zc-text);
  word-break: break-all;
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