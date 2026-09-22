<script setup>
// 预览日志面板：REST 拉历史（平台步骤行 + dev 栈容器输出）+ /ws/deploy/{id} 追实时。
//
// 为什么两路都要：环形缓冲与容器日志回答的是「刚才那次启动走到哪一步」，
// WS 回答的是「此刻正在发生的这一步」——只有 REST 就得手点刷新，只有 WS 就看不到历史。
// 两边共用同一套 seq（DeployHub 发事件时盖的号），重叠部分按 seq 去重（见 utils/deployLog）。
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import {
  ClearOutlined, CopyOutlined, ReloadOutlined,
} from '@ant-design/icons-vue'
import { deployWsUrl, devLogs } from '../api'
import {
  appendRows, formatRow, lineKind, rowFromEvent, statusText, tsShort,
} from '../utils/deployLog'

const props = defineProps({
  appId: { type: [Number, String], default: null },
  appName: { type: String, default: '' },
  active: { type: Boolean, default: false },
})

const lines = ref([])
const note = ref('')
const status = ref('stopped')
const url = ref('')
const loading = ref(false)
const live = ref(false)          // WS 是否连着（面板上的「实时 / 快照」）
const autoScroll = ref(true)
const box = ref(null)
let lastSeq = 0
let ws = null
let retryTimer = null
let retries = 0

const asText = computed(() => lines.value.map(formatRow).join('\n'))
const copied = ref(false)

async function scrollBottom() {
  await nextTick()
  if (autoScroll.value && box.value) box.value.scrollTop = box.value.scrollHeight
}

function push(rows) {
  const merged = appendRows(lines.value, rows, { lastSeq })
  lines.value = merged.rows
  lastSeq = merged.lastSeq
  scrollBottom()
}

/** 拉一次日志（打开面板 / 点刷新）。整份以服务端快照为准重建，避免两套编号错位。 */
async function refresh() {
  if (!props.appId) return
  loading.value = true
  try {
    const { data } = await devLogs(props.appId, 300)
    lines.value = data.lines || []
    lastSeq = data.last_seq || 0
    note.value = data.note || ''
    status.value = data.status_dev || 'stopped'
    url.value = data.url || ''
    scrollBottom()
  } catch (e) {
    note.value = e?.response?.data?.detail || '日志加载失败'
  } finally {
    loading.value = false
  }
}

/** WS 帧处理：单独列出来既给测试用，也让「忽略 ping/hello」只有一处定义。 */
function handleEvent(ev) {
  if (!ev || typeof ev !== 'object') return
  if (ev.type === 'dev_status') {
    status.value = ev.status || status.value
    return
  }
  if (ev.type === 'migrate_result') {
    push([{ seq: 0, ts: new Date().toISOString(), source: 'platform', service: '平台',
            line: `[zerocode] ${ev.ok ? '✓' : '✗'} 迁移${ev.ok ? '成功' : '失败'}（${ev.env || 'dev'}）` }])
    return
  }
  if (ev.type === 'dev_reset_result') {
    push([{ seq: 0, ts: new Date().toISOString(), source: 'platform', service: '平台',
            line: `[zerocode] ${ev.ok ? '✓' : '✗'} dev 库重建${ev.ok ? '完成' : '失败'}（${ev.env || 'dev'}）` }])
    return
  }
  const row = rowFromEvent(ev)
  if (row) push([row])
}

function connect() {
  if (!props.appId || ws || typeof WebSocket === 'undefined') return
  ws = new WebSocket(deployWsUrl(props.appId))
  ws.onopen = () => { live.value = true; retries = 0 }
  ws.onmessage = (m) => {
    try {
      handleEvent(JSON.parse(m.data))
    } catch { /* 非 JSON 帧忽略 */ }
  }
  ws.onclose = () => {
    live.value = false
    ws = null
    // 面板还开着就续上：预览启动过程往往正是最需要看的时候，断一次就永远停在快照很难看。
    if (props.active && retries < 3) {
      retries += 1
      retryTimer = setTimeout(connect, Math.min(500 * retries, 2000))
    }
  }
  ws.onerror = () => { /* onclose 紧接着来，重连统一在那儿收口 */ }
}

function disconnect() {
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  retries = 0
  if (ws) {
    const sock = ws
    ws = null                 // 先摘引用：这里主动 close 不该再排一次重连
    sock.onclose = null
    sock.onmessage = null
    try { sock.close() } catch { /* 已关就忽略 */ }
  }
  live.value = false
}

async function copyAll() {
  try {
    await navigator.clipboard.writeText(asText.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    note.value = '复制失败：浏览器拒绝了剪贴板权限'
  }
}

function clearView() {
  lines.value = []
  note.value = ''
}

watch(() => [props.active, props.appId], async ([open]) => {
  if (open) {
    await refresh()
    connect()
  } else {
    disconnect()
  }
}, { immediate: true })

onUnmounted(disconnect)

defineExpose({ refresh, handleEvent, connect, disconnect, lines, status, note, live })
</script>

<template>
  <div class="zc-dlog">
    <div class="zc-dlog-bar">
      <a-space>
        <a-tag :color="status === 'running' ? 'green' : (status === 'error' ? 'red' : 'default')">
          {{ statusText(status) }}
        </a-tag>
        <span class="zc-dlog-mode">{{ live ? '实时' : '快照' }}</span>
        <a v-if="url && status === 'running'" :href="url" target="_blank">{{ url }}</a>
      </a-space>
      <a-space>
        <a-button size="small" :loading="loading" @click="refresh">
          <reload-outlined /> 刷新
        </a-button>
        <a-button size="small" @click="copyAll">
          <copy-outlined /> {{ copied ? '已复制' : '复制全部' }}
        </a-button>
        <a-button size="small" @click="clearView"><clear-outlined /> 清空视图</a-button>
        <a-checkbox v-model:checked="autoScroll">自动滚底</a-checkbox>
      </a-space>
    </div>
    <p v-if="note" class="zc-dlog-note">{{ note }}</p>
    <div ref="box" class="zc-dlog-body">
      <p v-if="!lines.length && !loading" class="zc-dlog-empty">
        暂无日志。点「启动预览」后再看这里，或先确认 Docker Desktop 已启动。
      </p>
      <pre v-for="(l, i) in lines" :key="`${l.seq || 0}-${i}`" class="zc-dlog-line" :class="lineKind(l)">
        <span class="zc-dlog-ts">{{ tsShort(l.ts) }}</span><span
          class="zc-dlog-svc">{{ l.service || (l.source === 'container' ? '容器' : '平台') }}</span><span
          class="zc-dlog-txt">{{ l.line }}</span>
      </pre>
    </div>
  </div>
</template>

<style scoped>
/* 素雅低饱和：深灰底 + 灰蓝/灰绿/灰红，长时间盯着不刺眼；等宽字体保证日志对齐 */
.zc-dlog { display: flex; flex-direction: column; height: 100%; gap: 8px; }
.zc-dlog-bar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.zc-dlog-mode { font-size: 12px; color: #8b95a1; }
.zc-dlog-note { margin: 0; font-size: 12px; color: #c08a3e; }
.zc-dlog-body {
  flex: 1; overflow: auto; background: #23272e; border-radius: 6px; padding: 10px 12px;
  font-family: ui-monospace, Consolas, Menlo, monospace; font-size: 12px; line-height: 1.7;
  max-height: calc(100vh - 200px);
}
.zc-dlog-empty { color: #9aa0a6; margin: 0; }
.zc-dlog-line { margin: 0; white-space: pre-wrap; word-break: break-all; color: #d8dade; }
.zc-dlog-ts { color: #7c8590; margin-right: 8px; }
.zc-dlog-svc { color: #8b95a1; margin-right: 8px; }
.zc-dlog-line.step { color: #b9c2cc; }
.zc-dlog-line.out { color: #9aa0a6; }
.zc-dlog-line.ok { color: #7fa88f; }
.zc-dlog-line.err { color: #c98a8a; }
</style>
