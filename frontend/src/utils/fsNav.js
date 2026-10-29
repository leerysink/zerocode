// 内置「类桌面」文件管理器（WorkspaceExplorer）的纯逻辑。
// 路径契约跟 GET /api/fs/ls 对齐：一律是工作区内的相对 posix 路径，工作区根为空串（后端不吐绝对路径）。
// 只放无副作用的换算，请求与渲染留在组件里。

// 当前焦点层：在「看得见的那几层」里挑最深的一层（同深度取先出现的，保证面包屑不随点击顺序乱跳）。
// 只算可见层：收起 src 后，ant 并不会把 src/lib 从 expandedKeys 里带走，但树里已经看不见它了，
// 面包屑继续指着 lib 就是在骗用户 —— 所以各级祖先没全展开的层直接跳过。
// 根是空串，它的“深度”算 0 而不是 1 —— 直接 split('/').length 会把 'src' 看成和根同深，
// 结果收起一级后面包屑反而退不回去。
export function deepestPath(keys) {
  const list = keys || []
  let best = ''
  let bestDepth = 0
  for (const k of list) {
    if (!k) continue
    if (!ancestorsOf(k).every((a) => list.includes(a))) continue
    const depth = k.split('/').length
    if (depth > bestDepth) {
      best = k
      bestDepth = depth
    }
  }
  return best
}

// /fs/ls 的一层条目 → a-tree 节点。目录不给 children（留空才能“展开时才拉这一层”），
// 文件是叶子；size/mtime 一并带上，右侧预览区要展示元信息，不再回查一次。
export function toTreeNodes(entries) {
  return (entries || []).map((e) => ({
    key: e.path,
    title: e.name,
    isLeaf: !e.is_dir,
    dir: !!e.is_dir,
    size: e.size,
    mtime: e.mtime,
  }))
}

// 懒加载回填：把 path 那一层的条目挂成它的 children。只重建命中路径上的那几个节点，
// 同层无关节点直接复用引用；找不到目标（目录已被外部删掉）就原样返回，不凭空造节点。
export function attachChildren(nodes, path, entries) {
  const walk = (list) => {
    let hit = false
    const out = list.map((n) => {
      if (n.key === path) {
        hit = true
        return { ...n, children: toTreeNodes(entries) }
      }
      if (n.children && n.children.length) {
        const kids = walk(n.children)
        if (kids !== n.children) {
          hit = true
          return { ...n, children: kids }
        }
      }
      return n
    })
    return hit ? out : list
  }
  return walk(nodes || [])
}

// 按 key 找节点：组件只拿到 selectedKeys，得自己把节点元信息（大小/时间/是否目录）找回来
export function findNode(nodes, key) {
  for (const n of nodes || []) {
    if (n.key === key) return n
    if (n.children && n.children.length) {
      const hit = findNode(n.children, key)
      if (hit) return hit
    }
  }
  return null
}

// 某一层要显示出来，得把它的各级祖先展开（不含自身：自身只是被看到，不是被展开）
export function ancestorsOf(path) {
  if (!path) return []
  const parts = path.split('/')
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'))
}

// 面包屑：首项恒为工作区根本身（path=''，点击即回到根层），其后逐级拼出完整可点路径
export function buildBreadcrumb(workspaceName, path) {
  const crumbs = [{ label: workspaceName || '工作区', path: '' }]
  const parts = path ? path.split('/') : []
  parts.forEach((_, i) => crumbs.push({ label: parts[i], path: parts.slice(0, i + 1).join('/') }))
  return crumbs
}

// 大小列：目录不给字节数（递归求和既不现实也误导），size 缺失同样给占位符而不是谎报 0 B，
// 其余按 1024 进制保留一位（负数这种脏数据按 0 B 收口，不渲染成 -5 B）
export function formatBytes(size, isDir) {
  if (isDir) return '—'
  if (size === null || size === undefined || size === '') return '—'
  let n = Number(size)
  if (!Number.isFinite(n)) return '—'
  if (n <= 0) return '0 B'
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

// 下载走浏览器原生 <a href>（不引 blob/axios）：鉴权靠同源 cookie/无鉴权本地服务，够轻也够稳。
// inline=1 让后端省掉 attachment 头，图片才能直接进 <img>（Markdown 渲染视图用）。
export function downloadUrl(workspaceId, path, inline = false) {
  const base = `/api/fs/download?workspace_id=${workspaceId}&path=${encodeURIComponent(path)}`
  return inline ? `${base}&inline=1` : base
}
