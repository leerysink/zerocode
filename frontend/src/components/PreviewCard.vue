<script setup>
// 预览工具结果卡：preview_status / preview_logs / preview_restart 共用一张卡。
// 一切读数都来自后端结果字段（status / healthy / probe / hint / error / url / note），
// 前端不拼预览地址——远程部署下拼出来的地址指向的是访问者自己的机器，端口再对也没用。
// 进度（progress）是临时事件，只挂在「还没有结果」的那张卡上，刷新后由结果重画终态。
import { computed, ref } from 'vue'

const props = defineProps({
  msg: { type: Object, required: true },
  progress: { type: Object, default: null },
})

const RUNNING_TITLE = {
  preview_status: '正在读取预览状态…',
  preview_logs: '正在读取预览日志…',
  preview_restart: '正在重启预览…',
}

// msg 一律是归一化后的记录（name/args/result），实时与历史同形，见 stores/messageModel.js
const name = computed(() => props.msg.name || '')
const result = computed(() => props.msg.result ?? null)

const tone = computed(() => {
  const r = result.value
  if (!r) return 'info'
  if (r.cancelled) return 'muted'
  if (r.ok === false) return r.error ? 'error' : 'warn'
  if (name.value === 'preview_logs') return 'plain'
  return r.status === 'running' && r.healthy ? 'ok' : 'warn'
})

const title = computed(() => {
  const r = result.value
  if (!r) return RUNNING_TITLE[name.value] || '正在检查预览…'
  if (r.cancelled) return '已停止等待'
  if (r.ok === false) return r.error ? '预览操作失败' : '仍在启动'
  if (name.value === 'preview_logs') return `预览日志（${(r.lines || []).length} 行）`
  const ready = r.status === 'running' && r.healthy
  if (name.value === 'preview_status') return ready ? '预览运行中' : '预览未就绪'
  return ready ? '预览已就绪' : '预览未就绪'
})

const detail = computed(() => {
  const r = result.value
  if (!r) return [props.progress?.line || '等待平台步骤…']
  const parts = []
  if (r.error) parts.push(r.error)
  if (r.hint) parts.push(r.hint)
  if (r.probe) parts.push(`探测：${r.probe}`)
  return parts
})

const logs = computed(() => {
  const r = result.value
  return (r && (r.lines || r.logs)) || []
})

// 只有「真在跑、探到健康、后端又给了地址」才给链接：healthy=false 时给一个打得开的链接，
// 等于把「HTTP 不可达」藏起来让用户自己撞 403。
const url = computed(() => {
  const r = result.value
  if (!r || r.ok === false || r.cancelled) return ''
  return r.status === 'running' && r.healthy && r.url ? r.url : ''
})

const note = computed(() => (result.value && result.value.note) || '')
const redacted = computed(() => (result.value && result.value.redacted) || 0)
const truncated = computed(() => !!(result.value && result.value.truncated))
const leftHint = computed(() => {
  const left = result.value && result.value.restarts_left
  return typeof left === 'number' && left >= 0 && left <= 1 ? `本次任务还可重启 ${left} 次` : ''
})
const open = ref(false)
</script>

<template>
  <div class="zc-preview" :class="`is-${tone}`">
    <div class="zc-preview-head">
      <span class="zc-preview-title">{{ title }}</span>
      <span v-if="name" class="zc-preview-tool">{{ name }}</span>
      <a v-if="url" class="zc-preview-link" :href="url" target="_blank" rel="noopener">打开预览</a>
    </div>
    <div v-for="(line, i) in detail" :key="`d${i}`" class="zc-preview-detail">{{ line }}</div>
    <div v-if="leftHint" class="zc-preview-note">{{ leftHint }}</div>
    <div v-if="redacted" class="zc-preview-note">已遮断 {{ redacted }} 处凭据</div>
    <div v-if="truncated" class="zc-preview-note">日志过长，已丢掉最早的行</div>
    <div v-if="note" class="zc-preview-note">{{ note }}</div>
    <button v-if="logs.length" class="zc-preview-toggle" type="button" @click="open = !open">
      {{ open ? '收起日志' : `查看日志（${logs.length}）` }}
    </button>
    <ul v-if="open && logs.length" class="zc-preview-logs">
      <li v-for="(line, i) in logs" :key="i">{{ line }}</li>
    </ul>
  </div>
</template>

<style scoped>
.zc-preview {
  width: 100%;
  max-width: 640px;
  border: 1px solid var(--zc-border);
  border-left: 3px solid var(--zc-border);
  border-radius: 8px;
  padding: 8px 12px;
  background: #fbfcfd;
}
.zc-preview.is-ok {
  border-left-color: #52a06b;
  background: #f6fbf7;
}
.zc-preview.is-warn {
  border-left-color: #d99a3c;
  background: #fdfaf3;
}
.zc-preview.is-error {
  border-left-color: #c4636a;
  background: #fdeeee;
}
.zc-preview.is-info {
  border-left-color: var(--zc-primary);
  background: #f4f8fd;
}
.zc-preview.is-muted {
  border-left-color: #b6bfca;
}
.zc-preview-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.zc-preview-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--zc-text);
}
.zc-preview-tool {
  font-size: 11px;
  color: var(--zc-text-sub);
  background: rgba(91, 122, 157, 0.08);
  border-radius: 4px;
  padding: 1px 6px;
}
.zc-preview-link {
  margin-left: auto;
  font-size: 12px;
  color: var(--zc-primary);
}
.zc-preview-detail {
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--zc-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-preview-note {
  margin-top: 4px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-preview-toggle {
  margin-top: 6px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12px;
  color: var(--zc-primary);
  cursor: pointer;
}
.zc-preview-logs {
  margin: 6px 0 0;
  padding: 8px 10px;
  max-height: 240px;
  overflow: auto;
  list-style: none;
  background: #f6f8fa;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  color: var(--zc-text);
}
.zc-preview-logs li {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
