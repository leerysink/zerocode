<script setup>
// preview_page 结果卡（规格 2026-10-31 §5）：标题、可见文本、控制台、失败请求、
// 首屏截图与视觉诊断。截图是受鉴权接口，直接给 <img src>；图被会话清理后降级成提示。
import { computed, ref } from 'vue'

const props = defineProps({
  msg: { type: Object, required: true },
  progress: { type: Object, default: null },
})

const result = computed(() => props.msg.result ?? props.msg.tool_result ?? null)
const name = computed(() => props.msg.name ?? props.msg.tool_name ?? 'preview_page')
const expanded = ref(false)
const shotBroken = ref(false)

const issues = computed(() => {
  const r = result.value
  if (!r) return 0
  return (r.console || []).length + (r.page_errors || []).length + (r.failed_requests || []).length
})

const tone = computed(() => {
  const r = result.value
  if (!r) return 'info'
  if (r.ok === false) return r.status && r.status !== 'running' ? 'muted' : 'error'
  if (r.timed_out) return 'warn'
  return issues.value ? 'warn' : 'ok'
})

const title = computed(() => {
  const r = result.value
  if (!r) return '正在打开预览页…'
  if (r.ok === false) {
    if (r.status && r.status !== 'running') return '预览未就绪'
    return r.timed_out ? '页面检查超时' : '页面检查失败'
  }
  return issues.value ? '页面已打开（发现异常）' : '页面检查完成'
})

const appLabel = computed(() => {
  const app = result.value?.app
  if (!app) return ''
  return [app.slug, app.stack].filter(Boolean).join(' · ')
})

const url = computed(() => (result.value?.ok ? result.value?.url : ''))
const pageTitle = computed(() => (result.value?.ok ? result.value?.title : ''))
const text = computed(() => (result.value?.ok ? result.value?.text || '' : ''))
const textLong = computed(() => text.value.length > 480)
const visionText = computed(() => {
  const v = result.value?.vision
  return v?.supported && v.text ? v.text : ''
})
const visionNote = computed(() => {
  const v = result.value?.vision
  if (!v) return ''
  if (!v.supported) return v.note || '当前模型不支持视觉，已跳过看图诊断'
  return v.error || ''
})
const consoleItems = computed(() => (result.value?.console || []))
const pageErrors = computed(() => (result.value?.page_errors || []))
const failedRequests = computed(() => (result.value?.failed_requests || []))
const screenshot = computed(() => (shotBroken.value ? null : result.value?.screenshot || null))
const notes = computed(() => {
  const r = result.value
  if (!r) return []
  const out = []
  if (r.redacted) out.push(`已遮断 ${r.redacted} 处凭据`)
  if (r.truncated) out.push('内容较长，已按预算截断')
  if (r.screenshot_note) out.push(r.screenshot_note)
  if (r.timed_out) out.push('页面加载超时，以下是已渲染的部分内容')
  return out
})
const loadingLine = computed(() => props.progress?.line || '正在打开预览页，等待页面渲染…')
</script>

<template>
  <div class="zc-page" :class="`is-${tone}`">
    <div class="zc-page-head">
      <span class="zc-page-title">{{ title }}</span>
      <span class="zc-page-tool">{{ name }}</span>
      <span v-if="issues" class="zc-page-badge">{{ issues }} 个问题</span>
      <a v-if="url" class="zc-page-link" :href="url" target="_blank" rel="noopener">打开预览</a>
    </div>

    <div v-if="!result" class="zc-page-loading">{{ loadingLine }}</div>
    <template v-else>
      <div v-if="result.ok === false" class="zc-page-error">{{ result.error || '页面检查失败' }}</div>
      <div v-if="appLabel" class="zc-page-meta">{{ appLabel }}<span v-if="result.status"> · {{ result.status }}</span></div>
      <div v-if="pageTitle" class="zc-page-pagetitle">{{ pageTitle }}</div>
      <div v-if="visionText" class="zc-page-vision">{{ visionText }}</div>
      <div v-else-if="visionNote" class="zc-page-note">{{ visionNote }}</div>

      <div v-if="text" class="zc-page-text-wrap">
        <pre class="zc-page-text" :class="{ 'is-clamped': textLong && !expanded }">{{ text }}</pre>
        <button v-if="textLong" class="zc-page-toggle" type="button" @click="expanded = !expanded">
          {{ expanded ? '收起正文' : '展开正文' }}
        </button>
      </div>

      <div v-if="consoleItems.length" class="zc-page-section">
        <div class="zc-page-section-title">控制台（{{ consoleItems.length }}）</div>
        <ul class="zc-page-list">
          <li v-for="(item, i) in consoleItems" :key="`c${i}`" :class="`is-${item.level}`">
            <span class="zc-page-level">{{ item.level }}</span>{{ item.text }}
          </li>
        </ul>
      </div>

      <div v-if="pageErrors.length" class="zc-page-section">
        <div class="zc-page-section-title">页面异常（{{ pageErrors.length }}）</div>
        <ul class="zc-page-list">
          <li v-for="(item, i) in pageErrors" :key="`p${i}`" class="is-error">{{ item }}</li>
        </ul>
      </div>

      <div v-if="failedRequests.length" class="zc-page-section">
        <div class="zc-page-section-title">失败请求（{{ failedRequests.length }}）</div>
        <ul class="zc-page-list">
          <li v-for="(item, i) in failedRequests" :key="`f${i}`">
            <span class="zc-page-method">{{ item.method }}</span>
            <span class="zc-page-status" :class="{ 'is-bad': item.status >= 400 }">
              {{ item.blocked ? 'blocked' : (item.status || 'failed') }}
            </span>
            <span class="zc-page-url">{{ item.url }}</span>
          </li>
        </ul>
      </div>

      <div v-if="screenshot" class="zc-page-shot">
        <a-image :src="screenshot.url" :width="320" alt="页面截图" @error="shotBroken = true" />
      </div>
      <div v-else-if="result.screenshot === null && result.ok" class="zc-page-note">本次没有可用截图</div>

      <div v-for="(note, i) in notes" :key="`n${i}`" class="zc-page-note">{{ note }}</div>
    </template>
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
.zc-page.is-warn {
  border-left-color: #d99a3c;
  background: #fdfaf3;
}
.zc-page.is-error {
  border-left-color: #c4636a;
  background: #fdeeee;
}
.zc-page.is-info {
  border-left-color: var(--zc-primary);
  background: #f4f8fd;
}
.zc-page.is-muted {
  border-left-color: #b6bfca;
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
.zc-page-badge {
  font-size: 11px;
  color: #a17c5b;
  background: rgba(161, 124, 91, 0.12);
  border-radius: 999px;
  padding: 1px 8px;
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
.zc-page-error {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: #a05a5a;
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-page-pagetitle {
  margin-top: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--zc-text);
}
.zc-page-vision {
  margin-top: 8px;
  padding: 8px 10px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--zc-text);
  background: rgba(91, 122, 157, 0.06);
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-page-text-wrap {
  margin-top: 8px;
}
.zc-page-text {
  margin: 0;
  padding: 8px 10px;
  max-height: 260px;
  overflow: auto;
  background: #fff;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  color: var(--zc-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-page-text.is-clamped {
  max-height: 120px;
}
.zc-page-toggle {
  margin-top: 4px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12px;
  color: var(--zc-primary);
  cursor: pointer;
}
.zc-page-section {
  margin-top: 10px;
}
.zc-page-section-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--zc-text-sub);
}
.zc-page-list {
  margin: 4px 0 0;
  padding: 6px 10px;
  max-height: 220px;
  overflow: auto;
  list-style: none;
  background: #fff;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  color: var(--zc-text);
}
.zc-page-list li {
  display: flex;
  gap: 6px;
  align-items: baseline;
  white-space: pre-wrap;
  word-break: break-all;
}
.zc-page-level {
  flex: none;
  font-size: 10px;
  text-transform: uppercase;
  color: #a05a5a;
}
.zc-page-list li.is-warning .zc-page-level {
  color: #a17c5b;
}
.zc-page-method {
  flex: none;
  font-weight: 600;
}
.zc-page-status {
  flex: none;
  color: var(--zc-text-sub);
}
.zc-page-status.is-bad {
  color: #a05a5a;
}
.zc-page-url {
  min-width: 0;
  color: var(--zc-text);
}
.zc-page-shot {
  margin-top: 10px;
  overflow: hidden;
  border-radius: 8px;
}
.zc-page-shot :deep(.ant-image) {
  max-width: 100%;
}
.zc-page-shot :deep(img) {
  max-width: 100%;
  height: auto;
  display: block;
}
</style>
