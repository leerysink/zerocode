// 全局时间格式化：相对时间（刚刚/N分钟前/N小时前/N天前），超过 30 天返回 YYYY-MM-DD
export function relativeTime(iso, now = Date.now()) {
  const diff = now - new Date(iso).getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}分钟前`
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)}小时前`
  if (diff <= 30 * 86400_000) return `${Math.floor(diff / 86400_000)}天前`
  return String(iso).slice(0, 10) // >30 天：取 ISO 日期部分 YYYY-MM-DD
}
