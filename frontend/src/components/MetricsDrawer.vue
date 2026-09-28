<script setup>
// 应用资源监控抽屉（Ticket 001）：本期只呈现工作区磁盘当前占用 + prod 运行态。
// CPU/内存/db_size 与历史曲线由后续票在同一响应形状上扩字段，本组件届时补渲染、接口不变。
// 打开时拉一次 current；实时刷新留给后续票（15s 轮询 current），本期先取一次即够。
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { appMetricsCurrent } from '../api'

const props = defineProps({ app: { type: Object, required: true } })

const loading = ref(true)
const data = ref(null)

async function load() {
  loading.value = true
  try {
    data.value = (await appMetricsCurrent(props.app.id)).data
  } catch (e) {
    message.error(e?.response?.data?.detail || '资源数据加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(load)

// 字节 → 人类可读（KB/MB/GB，二进制步进）；无值显示占位。
function fmtBytes(n) {
  if (n == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = Number(n)
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1 }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`
}

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
</script>

<template>
  <a-spin v-if="loading" />
  <div v-else-if="data" class="zc-metrics">
    <div class="zc-metric">
      <span class="zc-metric-label">工作区磁盘占用</span>
      <span class="zc-metric-value">{{ fmtBytes(data.workspace_size?.value) }}</span>
      <span v-if="data.workspace_size?.ts" class="zc-metric-ts">采集于 {{ fmtTs(data.workspace_size.ts) }}</span>
      <span v-else class="zc-metric-ts">尚未采集</span>
    </div>
    <div class="zc-metric">
      <span class="zc-metric-label">生产环境状态</span>
      <span class="zc-metric-value">{{ prodText(data.prod_status) }}</span>
    </div>
  </div>
  <a-empty v-else description="暂无数据" />
</template>

<style scoped>
.zc-metrics { display: flex; flex-direction: column; gap: 14px; }
.zc-metric { display: flex; flex-direction: column; gap: 4px; }
.zc-metric-label { font-size: 12px; color: var(--zc-text-sub); }
.zc-metric-value { font-size: 18px; font-weight: 600; }
.zc-metric-ts { font-size: 11px; color: var(--zc-text-sub); }
</style>
