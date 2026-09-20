// 预览日志面板的纯逻辑：行着色分类、时间格式化、REST 历史与 WS 实时的去重合并。
//
// 为什么单独成 utils：这三件事都有「错了不报错、只是看起来怪」的性质（重复行、按错颜色、
// 面板无限增长），是单测最划算的那类；组件只留接线。
export const MAX_LINES = 2000        // 面板上限：dev 栈长跑几天也不会把浏览器拖死

export const STATUS_TEXT = {
  running: '运行中', stopped: '已停止', starting: '启动中…', stopping: '停止中…',
  publishing: '发布中…', error: '出错啦', unhealthy: '体检未通过',
}

export const statusText = (s) => STATUS_TEXT[s || 'stopped'] || s || '已停止'

/** ISO 串 → `HH:MM:SS`（本地时区）；解析不出来就原样留空，日志面板不该因脏数据炸掉。 */
export function tsShort(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/**
 * 行种类（决定着色）：失败/告警 → err，成功/就绪 → ok，
 * 平台步骤行 → step，其余为容器原始输出 → out。
 * 判据用「词面」而不是 source：平台步骤行与转发进来的迁移/compose 行同属一条 dev_log 流。
 */
export function lineKind(row) {
  const t = String((row && row.line) || '')
  if (/[✗×]|ERROR|Traceback|Failed|failed|失败|不可用|WARNING|WARN|error/.test(t)) return 'err'
  if (/[✓]|就绪|成功|完成|Created|Started/.test(t)) return 'ok'
  return row && row.source === 'container' ? 'out' : 'step'
}

/**
 * 把新一批行并进来：带 `seq` 的行按 seq 去重（REST 历史与 WS 实时会重叠，seq 同源单调递增），
 * 无 seq 的实时行按「内容 + 时钟」直接追加；总数裁到最近 maxLines 条。
 * 返回 `{ rows, lastSeq }`——lastSeq 回给下次增量拉取用。
 */
export function appendRows(current, rows, { max = MAX_LINES, lastSeq = 0 } = {}) {
  const seen = new Set()
  for (const r of current) if (r.seq) seen.add(r.seq)
  let from = lastSeq
  const next = current.slice()
  for (const r of rows || []) {
    if (r && r.seq) {
      if (seen.has(r.seq)) continue
      seen.add(r.seq)
      from = Math.max(from, r.seq)
    }
    next.push(r)
  }
  return { rows: next.length > max ? next.slice(-max) : next, lastSeq: from }
}

/** WS 事件 → 面板一行。非日志事件（dev_status/ping/hello）返回 null，由调用方忽略。 */
export function rowFromEvent(ev, ts = new Date().toISOString()) {
  if (!ev || ev.type !== 'dev_log') return null
  return { seq: ev.seq || 0, ts, source: 'platform', service: '平台', line: String(ev.line ?? '') }
}

/** 面板渲染文本（复制全部用）：`时间 来源 | 内容`，无 ts/来源时降级成裸行。 */
export function formatRow(row) {
  const head = [tsShort(row.ts), row.service].filter(Boolean).join(' ')
  return head ? `${head} | ${row.line}` : String(row.line ?? '')
}
