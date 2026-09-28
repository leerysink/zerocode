<script setup>
// 应用资源监控抽屉（Ticket 001–006）：每类指标一行——当前值 + 该指标的历史 sparkline，
// 顶部一条窗口切换（1h/24h/7d/30d）统一驱动所有历史曲线。
// 指标集固定为 cpu/memory/db_size/workspace_size（响应形状里缺哪类就显示「尚未采集」，接口恒定）。
// 图表用轻量内联 SVG（不引第三方库，保持离线、无新 npm 依赖）。
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { appMetricsCurrent, appMetricsHistory } from '../api'

const props = defineProps({ app: { type: Object, required: true } })

const WINDOWS = ['1h', '24h', '7d', '30d']
const loading = ref(true)
const data = ref(null)          // current：{ cpu, memory, db_size, workspace_size, prod_status }
const hist = ref({})            // 历史：{ cpu:[{ts,value}], ... }
const window_ = ref('24h')      // 当前窗口（window 是保留名，加下划线避免与全局冲突）
let histSeq = 0                 // history 请求序号：只接受最新一次响应，防快速切窗乱序覆盖

// 字节 → 人类可读（二进制步进）。
function fmtBytes(n) {
  if (n == null) return null
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = Number(n)
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1 }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`
}
function fmtPct(n) {
  return n == null ? null : `${Number(n).toFixed(1)} %`
}

// 指标清单：cpu/memory 来自 docker stats（Ticket 004），db_size 来自 PG（Ticket 005），
// workspace_size 来自文件系统（Ticket 001）。缺失的 kind 自动走「尚未采集」分支。
const METRICS = [
  { key: 'cpu', label: 'CPU 占用', fmt: fmtPct },
  { key: 'memory', label: '内存占用', fmt: fmtBytes },
  { key: 'db_size', label: '数据库磁盘', fmt: fmtBytes },
  { key: 'workspace_size', label: '工作区磁盘', fmt: fmtBytes },
]

// prod 状态中文映射（与 AppsView 口径对齐；缺失回退占位，不把英文原值/空白直打进界面）。
const PROD_STATUS_TEXT = {
  stopped: '已停止', starting: '启动中', running: '运行中',
  publishing: '发布中', error: '出错了', unhealthy: '体检未通过',
}
const prodText = (s) => PROD_STATUS_TEXT[s] || s || '—'

// 采集时刻本地化到秒；解析不了则退回原串。
function fmtTs(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

async function loadCurrent() {
  data.value = (await appMetricsCurrent(props.app.id)).data
}
async function loadHistory() {
  const my = ++histSeq
  const res = (await appMetricsHistory(props.app.id, window_.value)).data || {}
  if (my === histSeq) hist.value = res        // 旧请求晚返时丢弃，不覆盖新窗口数据
}
async function load() {
  loading.value = true
  try {
    await Promise.all([loadCurrent(), loadHistory()])
  } catch (e) {
    message.error(e?.response?.data?.detail || '资源数据加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function switchWindow(w) {
  if (w === window_.value) return
  window_.value = w
  try {
    await loadHistory()
  } catch (e) {
    message.error(e?.response?.data?.detail || '历史数据加载失败')
  }
}

// SVG 折线坐标：把某类时序归一化进 300×80 视口（y 轴留 4px 边距）。
// <2 点不构成折线（返空串），避免除零与畸形路径。
const VIEW_W = 300
const VIEW_H = 80
function toPoints(pts) {
  if (!pts || pts.length < 2) return ''
  const vals = pts.map((p) => Number(p.value))
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const span = max - min || 1
  const step = VIEW_W / (pts.length - 1)
  return pts.map((p, idx) => {
    const x = idx * step
    const y = VIEW_H - 4 - ((Number(p.value) - min) / span) * (VIEW_H - 8)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}
const lines = computed(() => {
  const out = {}
  for (const m of METRICS) out[m.key] = toPoints(hist.value[m.key])
  return out
})
</script>

<template>
  <a-spin v-if="loading" />
  <div v-else-if="data" class="zc-metrics">
    <div class="zc-metric zc-metric-status">
      <span class="zc-metric-label">生产环境状态</span>
      <span class="zc-metric-value">{{ prodText(data.prod_status) }}</span>
    </div>

    <div v-for="m in METRICS" :key="m.key" class="zc-metric zc-metric-chart">
      <div class="zc-chart-head">
        <span class="zc-metric-label">{{ m.label }}</span>
        <span class="zc-metric-value">{{ (data[m.key] && m.fmt(data[m.key].value)) || '—' }}</span>
      </div>
      <span v-if="data[m.key] && data[m.key].ts" class="zc-metric-ts">采集于 {{ fmtTs(data[m.key].ts) }}</span>
      <span v-else class="zc-metric-ts">尚未采集</span>
      <svg v-if="lines[m.key]" class="zc-sparkline" viewBox="0 0 300 80" preserveAspectRatio="none">
        <polyline class="zc-sparkline-path" :points="lines[m.key]" />
      </svg>
    </div>

    <div class="zc-window-tabs">
      <button v-for="w in WINDOWS" :key="w" type="button" class="zc-window-tab"
              :class="{ active: w === window_ }" @click="switchWindow(w)">{{ w }}</button>
    </div>
  </div>
  <a-empty v-else description="暂无数据" />
</template>

<style scoped>
.zc-metrics { display: flex; flex-direction: column; gap: 14px; }
.zc-metric { display: flex; flex-direction: column; gap: 4px; }
.zc-metric-status { padding-bottom: 6px; border-bottom: 1px solid var(--zc-border); }
.zc-metric-label { font-size: 12px; color: var(--zc-text-sub); }
.zc-metric-value { font-size: 16px; font-weight: 600; }
.zc-metric-ts { font-size: 11px; color: var(--zc-text-sub); }
.zc-metric-chart { gap: 4px; }
.zc-chart-head { display: flex; align-items: baseline; justify-content: space-between; }
.zc-window-tabs { display: flex; gap: 4px; margin-top: 4px; }
.zc-window-tab { border: 1px solid var(--zc-border); background: transparent; border-radius: 4px;
                 padding: 1px 8px; font-size: 12px; cursor: pointer; color: var(--zc-text-sub); }
.zc-window-tab.active { border-color: #5b7a9d; color: #5b7a9d; }
.zc-sparkline { width: 100%; height: 64px; margin-top: 2px; }
.zc-sparkline-path { fill: none; stroke: #5b7a9d; stroke-width: 1.5; }
</style>
