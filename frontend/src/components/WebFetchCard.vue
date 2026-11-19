<script setup>
// web_fetch 结果卡（ADR-0053 联网读取）：标题 / 描述 / 正文 / 页面链接 + 抓取元信息。
// 与 PageInspectCard / BrowserFlowCard 同一套卡壳与颜色 token：三张卡在消息流里是同一视觉语言。
// 正文与链接都来自外部页面，渲染一律按纯文本与带 rel 的锚点，不解析、不执行任何页面内容。
import { computed, ref } from 'vue'

const props = defineProps({
  msg: { type: Object, required: true },
  progress: { type: Object, default: null },
})

const result = computed(() => props.msg.result ?? props.msg.tool_result ?? null)
const expanded = ref(false)

// 请求的地址：成功时以后端回的最终地址（重定向后）为准，失败/进行中回退到调用参数
const argsUrl = computed(() => {
  const u = props.msg.args?.url
  return typeof u === 'string' ? u : ''
})
const ok = computed(() => result.value?.ok === true)
const tone = computed(() => {
  const r = result.value
  if (!r) return 'info'
  if (r.ok === false) return 'error'
  if (r.note) return 'warn' // 二进制内容：只回了元信息，没读正文
  return 'ok'
})
const title = computed(() => {
  const r = result.value
  if (!r) return '正在读取网页…'
  if (r.ok === false) return '网页读取失败'
  return r.title ? '网页已读取' : '网页内容已读取'
})
const url = computed(() => (ok.value ? result.value?.url || argsUrl.value : argsUrl.value))
const pageTitle = computed(() => (ok.value ? result.value?.title || '' : ''))
const description = computed(() => (ok.value ? result.value?.description || '' : ''))
const text = computed(() => (ok.value ? result.value?.text || '' : ''))
const textLong = computed(() => text.value.length > 600)
const links = computed(() => (ok.value ? result.value?.links || [] : []))
const LINK_FOLD = 8
const linksLong = computed(() => links.value.length > LINK_FOLD)
const shownLinks = computed(() => (expanded.value ? links.value : links.value.slice(0, LINK_FOLD)))

const metaLine = computed(() => {
  const r = result.value
  if (!ok.value) return ''
  const parts = []
  if (r.status) parts.push(`HTTP ${r.status}`)
  if (r.content_type) parts.push(r.content_type)
  if (r.bytes) parts.push(`${Math.max(1, Math.round(r.bytes / 1024))} KB`)
  const redirects = (r.redirects || []).length
  if (redirects) parts.push(`跳转 ${redirects} 次`)
  if (r.elapsed_ms) parts.push(`耗时 ${r.elapsed_ms}ms`)
  // -1 是「不限」（后端拿它表达额度未设），画成「还能抓 -1 次」就是假话
  if (typeof r.fetches_left === 'number' && r.fetches_left >= 0) {
    parts.push(`本次任务还能抓 ${r.fetches_left} 个页面`)
  }
  return parts.join(' · ')
})

const notes = computed(() => {
  const r = result.value
  if (!r || r.ok === false) return []
  const out = []
  if (r.note) out.push(r.note)
  if (r.redacted) out.push(`已遮断 ${r.redacted} 处凭据`)
  if (r.truncated || r.text_truncated) out.push('内容较长，已按预算截断')
  if (r.fetches_left === 0) out.push('本次任务的网页读取次数已用完')
  return out
})

const loadingLine = computed(() => props.progress?.line || '正在读取网页…')
const toolName = computed(() => props.msg.name ?? props.msg.tool_name ?? 'web_fetch')
</script>

<template>
  <div class="zc-page" :class="`is-${tone}`">
    <div class="zc-page-head">
      <span class="zc-page-title">{{ title }}</span>
      <span class="zc-page-tool">{{ toolName }}</span>
      <a v-if="url" class="zc-page-link" :href="url" target="_blank" rel="noopener noreferrer">
        打开原页
      </a>
    </div>

    <div v-if="!result" class="zc-page-loading">{{ loadingLine }}</div>
    <template v-else>
      <div v-if="result.ok === false" class="zc-page-error" data-test="fetch-error">
        {{ result.error || '网页读取失败' }}
      </div>
      <div v-if="url" class="zc-page-meta zc-page-url">{{ url }}</div>
      <div v-if="metaLine" class="zc-page-meta">{{ metaLine }}</div>
      <div v-if="pageTitle" class="zc-page-pagetitle">{{ pageTitle }}</div>
      <div v-if="description" class="zc-page-desc">{{ description }}</div>

      <div v-if="text" class="zc-page-text-wrap">
        <pre class="zc-page-text" :class="{ 'is-clamped': textLong && !expanded }">{{ text }}</pre>
        <button v-if="textLong" class="zc-page-toggle" type="button" @click="expanded = !expanded">
          {{ expanded ? '收起正文' : '展开正文' }}
        </button>
      </div>

      <div v-if="links.length" class="zc-page-section" data-test="links">
        <div class="zc-page-section-title">
          页面链接（{{ links.length }}{{ result.links_truncated ? '，已截断' : '' }}）
        </div>
        <ul class="zc-page-list">
          <li v-for="(item, i) in shownLinks" :key="`l${i}`">
            <a class="zc-page-anchor" :href="item.url" target="_blank" rel="noopener noreferrer">
              {{ item.text || item.url }}
            </a>
          </li>
        </ul>
        <button v-if="linksLong" class="zc-page-toggle" type="button" @click="expanded = !expanded">
          {{ expanded ? '收起链接' : `展开全部 ${links.length} 条` }}
        </button>
      </div>

      <div v-for="(note, i) in notes" :key="`n${i}`" class="zc-page-note">{{ note }}</div>
    </template>
  </div>
</template>

<style scoped>
/* 与 PageInspectCard / BrowserFlowCard 同一套卡壳与同一批颜色常量：不新增色值。 */
.zc-page {
  width: 100%;
  max-width: 720px;
  border: 1px solid var(--zc-border);
  border-left: 3px solid var(--zc-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--zc-bg-card-soft);
}
.zc-page.is-ok {
  border-left-color: var(--zc-success-bright);
  background: var(--zc-success-bg);
}
.zc-page.is-warn {
  border-left-color: var(--zc-warning-bright);
  background: var(--zc-warning-bg);
}
.zc-page.is-error {
  border-left-color: var(--zc-danger-bright);
  background: var(--zc-danger-bg);
}
.zc-page.is-info {
  border-left-color: var(--zc-primary);
  background: var(--zc-bg-info);
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
  background: rgba(var(--zc-primary-rgb), 0.08);
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
.zc-page-error {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--zc-danger-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-page-pagetitle {
  margin-top: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--zc-text);
}
.zc-page-desc {
  margin-top: 4px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--zc-text-sub);
  word-break: break-word;
}
.zc-page-text-wrap {
  margin-top: 8px;
}
.zc-page-text {
  margin: 0;
  padding: 8px 10px;
  max-height: 320px;
  overflow: auto;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--zc-text);
  background: var(--zc-bg-card);
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-page-text.is-clamped {
  max-height: 120px;
  overflow: hidden;
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
  background: var(--zc-bg-card);
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.7;
}
.zc-page-list li {
  white-space: pre-wrap;
  word-break: break-all;
}
.zc-page-anchor {
  color: var(--zc-primary);
}
</style>
