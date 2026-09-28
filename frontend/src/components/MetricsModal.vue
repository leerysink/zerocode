<script setup>
// 应用资源监控弹框（重设计）：居中 a-modal 的主体内容——分组 2×2 指标卡片
// （运行时 CPU/内存 + 存储 数据库/工作区），每卡 当前/均值/峰值 三值 + 一张精致内联 SVG 曲线。
// 曲线零第三方依赖（渐变面积 + 网格 + 末端当前点 + 绝对时间刻度），守住离线构建纪律。
// 均值/峰值由前端从窗口历史点直接算出，不改后端；接口沿用 appMetricsCurrent / appMetricsHistory。
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { appMetricsCurrent, appMetricsHistory } from '../api'

const props = defineProps({ app: { type: Object, required: true } })

const WINDOWS = ['1h', '24h', '7d', '30d']
const loading = ref(true)
const data = ref(null)          // current：{ cpu, memory, db_size, workspace_size, prod_status }
const hist = ref({})            // 历史：{ cpu:[{ts,value}], ... }
const window_ = ref('24h')      // 当前窗口（window 是保留名，加下划线避免与全局冲突）
const switching = ref(false)    // 切窗独立 pending：不共用 loading（它会整块掉工具条导致点不动）
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
const FMT = { pct: fmtPct, bytes: fmtBytes }

// prod 运行态白名单（与后端 LIVE_PROD_STATUSES 对齐）：停炉时 cpu/memory 无实时值。
const LIVE = ['running', 'unhealthy']

// 指标清单：分组、配色（走卡片 --accent，曲线/圆点/网格同色）、格式化器。
// cpu/memory 来自 docker stats（Ticket 004），db_size 来自 PG（005），workspace_size 来自文件系统（001）。
const METRICS = [
  { key: 'cpu', label: 'CPU 占用', fmt: 'pct', color: '#5b7a9d', group: 'runtime', runtime: true },
  { key: 'memory', label: '内存占用', fmt: 'bytes', color: '#5b8a72', group: 'runtime', runtime: true },
  { key: 'db_size', label: '数据库磁盘', fmt: 'bytes', color: '#c08a3e', group: 'storage' },
  { key: 'workspace_size', label: '工作区磁盘', fmt: 'bytes', color: '#8a7ba6', group: 'storage' },
]
// cpu 按百分比、其余按字节；fmt==='pct' 之外一律走字节格式化。
const fmtOf = (m) => (m.fmt === 'pct' ? FMT.pct : FMT.bytes)

const GROUP_TITLES = { runtime: '运行时', storage: '存储' }

// 采集时刻本地化：长窗（≥7d）带日期，短窗只到时分。解析不了退回原串。
function fmtAxis(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (window_.value === '7d' || window_.value === '30d') {
    return `${d.getMonth() + 1}/${d.getDate()} ${hm}`
  }
  return hm
}

// 内联 SVG 视口（所有曲线共用）：底部留白给时间刻度。
const VW = 320
const VH = 104
const PAD_X = 3
const PAD_T = 10
const PAD_B = 10
const BASE = VH - PAD_B

// 清洗时序：剔除 null/空洞与无值点，曲线与均值/峰值共用同一口径（两处必须同进同退）。
const clean = (series) => (series || []).filter((p) => p && p.value != null)

// 由某类时序算出精致曲线要素：折线点、闭合面积路径、末端当前点、起止时间刻度。
// <2 点不成图（ok=false）——曲线区退化为占位，胜过画畸形路径或除零。
function buildChart(pts) {
  if (pts.length < 2) return { ok: false }
  const vals = pts.map((p) => Number(p.value))
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const span = max - min || 1
  const stepX = (VW - 2 * PAD_X) / (pts.length - 1)
  const coords = pts.map((p, i) => ({
    x: PAD_X + i * stepX,
    y: BASE - ((Number(p.value) - min) / span) * (BASE - PAD_T),
  }))
  const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')
  const first = coords[0]
  const last = coords[coords.length - 1]
  const area = `M ${first.x.toFixed(1)},${BASE} L ${line.replace(/ /g, ' L ')} L ${last.x.toFixed(1)},${BASE} Z`
  return {
    ok: true, line, area, cursor: last,
    start: fmtAxis(pts[0].ts), end: fmtAxis(pts[pts.length - 1].ts),
  }
}

// 每张卡片的视图模型：当前值（或空态文案）+ 均值/峰值 + 曲线。保持 METRICS 顺序渲染。
const cards = computed(() => METRICS.map((m) => {
  const cur = data.value?.[m.key]
  const live = cur && cur.value != null
  const fmt = fmtOf(m)
  const runtimeEmpty = m.runtime && !LIVE.includes(data.value?.prod_status)
  const emptyText = live ? '' : (runtimeEmpty ? '无实时数据' : '尚未采集')
  const series = clean(hist.value[m.key])
  const nums = series.map((p) => Number(p.value)).filter((v) => Number.isFinite(v))
  const avg = nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null
  const peak = nums.length ? Math.max(...nums) : null
  return {
    ...m,
    hero: live ? fmt(cur.value) : '',
    emptyText,
    avg: avg == null ? '—' : (fmt(avg) ?? '—'),
    peak: peak == null ? '—' : (fmt(peak) ?? '—'),
    chart: buildChart(series),
    gid: `zc-grad-${m.key}`,
  }
}))

const groups = computed(() => {
  const order = ['runtime', 'storage']
  return order.map((g) => ({ title: GROUP_TITLES[g], items: cards.value.filter((c) => c.group === g) }))
})

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
  if (w === window_.value || switching.value) return
  const prev = window_.value
  window_.value = w
  switching.value = true
  try {
    await loadHistory()
  } catch (e) {
    window_.value = prev   // 失败回滚：别让旧窗口的曲线被错标成新窗口
    message.error(e?.response?.data?.detail || '历史数据加载失败')
  } finally {
    switching.value = false
  }
}
</script>

<template>
  <a-spin v-if="loading" class="zc-metrics-spin" />
  <a-spin v-else-if="data" :spinning="switching" class="zc-metrics-spinwrap">
  <div class="zc-metrics-modal">
    <div class="zc-metrics-toolbar">
      <span class="zc-toolbar-hint">近 {{ window_ }} 走势 · 均按应用级汇总</span>
      <div class="zc-window-tabs" role="group" aria-label="统计窗口">
        <button v-for="w in WINDOWS" :key="w" type="button" class="zc-window-tab"
                :class="{ active: w === window_ }" :aria-pressed="w === window_"
                :disabled="switching" @click="switchWindow(w)">{{ w }}</button>
      </div>
    </div>

    <section v-for="g in groups" :key="g.title" class="zc-metric-group" :aria-label="g.title">
      <div class="zc-group-title">{{ g.title }}</div>
      <div class="zc-metric-grid">
        <article v-for="c in g.items" :key="c.key" class="zc-metric-card" :style="{ '--accent': c.color }">
          <div class="zc-card-top">
            <span class="zc-card-dot" />
            <span class="zc-card-label">{{ c.label }}</span>
          </div>

          <div class="zc-card-hero">
            <span v-if="c.hero" class="zc-hero-val">{{ c.hero }}</span>
            <span v-else class="zc-hero-empty">{{ c.emptyText }}</span>
            <span class="zc-hero-cap">当前</span>
          </div>

          <div class="zc-card-stats">
            <div class="zc-stat"><span class="zc-stat-k">均值</span><span class="zc-stat-v">{{ c.avg }}</span></div>
            <div class="zc-stat"><span class="zc-stat-k">峰值</span><span class="zc-stat-v">{{ c.peak }}</span></div>
          </div>

          <div class="zc-chart">
            <svg v-if="c.chart.ok" class="zc-chart-svg" :viewBox="`0 0 ${VW} ${VH}`" preserveAspectRatio="none" aria-hidden="true">
              <defs>
                <linearGradient :id="c.gid" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="currentColor" stop-opacity="0.28" />
                  <stop offset="100%" stop-color="currentColor" stop-opacity="0.02" />
                </linearGradient>
              </defs>
              <line class="zc-grid" :x1="PAD_X" :y1="PAD_T + (BASE - PAD_T) * 0.25" :x2="VW - PAD_X" :y2="PAD_T + (BASE - PAD_T) * 0.25" />
              <line class="zc-grid" :x1="PAD_X" :y1="PAD_T + (BASE - PAD_T) * 0.5" :x2="VW - PAD_X" :y2="PAD_T + (BASE - PAD_T) * 0.5" />
              <line class="zc-grid" :x1="PAD_X" :y1="PAD_T + (BASE - PAD_T) * 0.75" :x2="VW - PAD_X" :y2="PAD_T + (BASE - PAD_T) * 0.75" />
              <path class="zc-chart-area" :d="c.chart.area" :fill="`url(#${c.gid})`" />
              <polyline class="zc-chart-line" :points="c.chart.line" />
              <circle class="zc-chart-cursor" :cx="c.chart.cursor.x" :cy="c.chart.cursor.y" r="3" />
            </svg>
            <div v-if="c.chart.ok" class="zc-chart-axis">
              <span class="zc-axis-start">{{ c.chart.start }}</span>
              <span class="zc-axis-end">{{ c.chart.end }}</span>
            </div>
            <div v-else class="zc-chart-blank">曲线数据不足</div>
          </div>
        </article>
      </div>
    </section>
  </div>
  </a-spin>
  <a-empty v-else description="暂无数据" />
</template>

<style scoped>
.zc-metrics-spin { display: block; margin: 48px auto; }
.zc-metrics-spinwrap { display: block; }
.zc-metrics-modal { display: flex; flex-direction: column; gap: 16px; }

/* 顶部工具条：左提示 + 右窗口分段切换 */
.zc-metrics-toolbar { display: flex; align-items: center; justify-content: space-between; }
.zc-toolbar-hint { font-size: 12px; color: var(--zc-text-sub); }
.zc-window-tabs { display: inline-flex; gap: 2px; padding: 2px; background: var(--zc-bg);
                  border: 1px solid var(--zc-border); border-radius: 8px; }
.zc-window-tab { border: 0; background: transparent; border-radius: 6px; padding: 3px 12px;
                 font-size: 12px; cursor: pointer; color: var(--zc-text-sub); transition: all 0.15s; }
.zc-window-tab:hover { color: var(--zc-primary); }
.zc-window-tab.active { background: #fff; color: var(--zc-primary); font-weight: 600;
                        box-shadow: 0 1px 3px rgba(47, 58, 71, 0.10); }

/* 分组标题 + 2 列卡片网格 */
.zc-metric-group { display: flex; flex-direction: column; gap: 10px; }
.zc-group-title { font-size: 12px; font-weight: 600; letter-spacing: 1px; color: var(--zc-text-sub); }
.zc-metric-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

/* 单卡：浅色浮起，顶部一条指标主色描边强化辨识 */
.zc-metric-card { position: relative; display: flex; flex-direction: column; gap: 8px;
                  padding: 14px 16px 12px; background: #fff; border: 1px solid var(--zc-border);
                  border-radius: 12px; box-shadow: 0 1px 4px rgba(47, 58, 71, 0.05); overflow: hidden; }
.zc-metric-card::before { content: ''; position: absolute; left: 0; top: 0; width: 100%; height: 3px;
                          background: var(--accent); opacity: 0.85; }
.zc-card-top { display: flex; align-items: center; gap: 8px; }
.zc-card-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); flex: 0 0 auto; }
.zc-card-label { font-size: 13px; color: var(--zc-text); font-weight: 500; }

.zc-card-hero { display: flex; align-items: baseline; gap: 8px; }
.zc-hero-val { font-size: 26px; font-weight: 700; line-height: 1.1; color: var(--zc-text); letter-spacing: 0.3px; }
.zc-hero-empty { font-size: 15px; font-weight: 500; color: var(--zc-text-sub); font-style: normal; }
.zc-hero-cap { font-size: 11px; color: var(--zc-text-sub); }

.zc-card-stats { display: flex; gap: 20px; }
.zc-stat { display: flex; flex-direction: column; gap: 1px; }
.zc-stat-k { font-size: 11px; color: var(--zc-text-sub); }
.zc-stat-v { font-size: 13px; font-weight: 600; color: var(--zc-text); }

/* 曲线：SVG 用 currentColor（= 卡片主色）描线与渐变填充 */
.zc-chart { display: flex; flex-direction: column; gap: 2px; }
.zc-chart-svg { width: 100%; height: 68px; color: var(--accent); display: block; }
.zc-grid { stroke: var(--zc-border); stroke-width: 1; }
.zc-chart-line { fill: none; stroke: currentColor; stroke-width: 1.6;
                 stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.zc-chart-cursor { fill: currentColor; stroke: #fff; stroke-width: 1.5; }
.zc-chart-axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--zc-text-sub); }
.zc-chart-blank { font-size: 11px; color: var(--zc-text-sub); text-align: center; padding: 18px 0;
                  background: var(--zc-bg); border-radius: 6px; }
</style>
