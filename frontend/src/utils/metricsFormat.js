// 指标读数格式化：资源监控弹框与应用页仪表卡共用同一把尺子——
// 两处若各写一份，「同一个内存数在两个界面显示成两个值」就是迟早的事。
// 空值一律返 null（不是 '0 B' / '0.0 %'）：后端刻意不为停炉栈写 0 假点，前端也不能把它画成零。

// 字节 → 人类可读（二进制步进）；字节档不带小数，跨档留一位。
export function fmtBytes(n) {
  if (n == null) return null
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = Number(n)
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1 }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`
}

// 百分比 → 固定一位小数（cpu 是多核汇总，可以超 100%，这里不封顶也不降位）。
export function fmtPct(n) {
  return n == null ? null : `${Number(n).toFixed(1)} %`
}
