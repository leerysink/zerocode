// 内置「类桌面」文件管理器（WorkspaceExplorer）的纯逻辑。
// 路径契约跟 GET /api/fs/ls 对齐：一律是工作区内的相对 posix 路径，工作区根为空串（后端不吐绝对路径）。
// 只放无副作用的换算，请求与渲染留在组件里。

// 上级路径：根没有上级（null），一级的上级是根（''）
export function parentPath(path) {
  if (!path) return null
  const i = path.lastIndexOf('/')
  return i < 0 ? '' : path.slice(0, i)
}

// 面包屑：首项恒为工作区根本身（path=''，点击即回到根层），其后逐级拼出完整可点路径
export function buildBreadcrumb(workspaceName, path) {
  const crumbs = [{ label: workspaceName || '工作区', path: '' }]
  const parts = path ? path.split('/') : []
  parts.forEach((_, i) => crumbs.push({ label: parts[i], path: parts.slice(0, i + 1).join('/') }))
  return crumbs
}

// 大小列：目录不给字节数（递归求和既不现实也误导），其余按 1024 进制保留一位
export function formatBytes(size, isDir) {
  if (isDir) return '—'
  let n = Number(size)
  if (!Number.isFinite(n) || n <= 0) return '0 B'
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let i = -1
  do {
    n /= 1024
    i++
  } while (n >= 1024 && i < units.length - 1)
  return `${n.toFixed(1)} ${units[i]}`
}

// 修改时间列：后端给的是 st_mtime（epoch 秒），按本地时区渲染成桌面惯例的 YYYY-MM-DD HH:mm
export function formatMtime(epochSeconds) {
  const n = Number(epochSeconds)
  if (!Number.isFinite(n) || n <= 0) return '—'
  const d = new Date(n * 1000)
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// 下载走浏览器原生 <a href>（不引 blob/axios）：鉴权靠同源 cookie/无鉴权本地服务，够轻也够稳
export function downloadUrl(workspaceId, path) {
  return `/api/fs/download?workspace_id=${workspaceId}&path=${encodeURIComponent(path)}`
}
