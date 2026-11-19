// 工作台页签的纯逻辑：打开/关闭/脏标记与持久化编解码。
// 页签只存路径，不存草稿——恢复后一律按磁盘内容重新读，避免旧缓冲覆盖别人（或 Agent）的改动。
// 所有变换都返回新对象，不改传入数组，组件里可以直接覆盖 ref。

const STORAGE_PREFIX = 'zc.workbench.tabs.v1.'

export function tabsStorageKey(workspaceId) {
  return `${STORAGE_PREFIX}${String(workspaceId ?? '')}`
}

export function getTab(tabs, path) {
  return (tabs || []).find((t) => t.path === path) || null
}

export function openTab(tabs, path) {
  const list = tabs || []
  if (list.some((t) => t.path === path)) return { tabs: list, active: path }
  return { tabs: [...list, { path, dirty: false }], active: path }
}

// 关闭后激活谁：右邻优先（与 VSCode 一致），没有右邻取左邻，最后一个关闭后没有激活项
export function neighborAfterClose(tabs, path) {
  const list = tabs || []
  const i = list.findIndex((t) => t.path === path)
  if (i === -1) return null
  if (i + 1 < list.length) return list[i + 1].path
  if (i > 0) return list[i - 1].path
  return null
}

export function closeTab(tabs, path, active) {
  const list = tabs || []
  const i = list.findIndex((t) => t.path === path)
  if (i === -1) return { tabs: list, active }
  const next = list.filter((t) => t.path !== path)
  if (active !== path) return { tabs: next, active }
  return { tabs: next, active: neighborAfterClose(list, path) ?? '' }
}

// 右键批量关闭的目标集合（按原顺序返回路径）：left/right 取锚点一侧，others 取除锚点外全部，
// all 取全部与锚点无关；锚点不存在时 left/right/others 为空。current 走单页签 closeTab，不在此列。
export function tabCloseSet(tabs, anchor, action) {
  const list = tabs || []
  if (action === 'all') return list.map((t) => t.path)
  const i = list.findIndex((t) => t.path === anchor)
  if (i === -1) return []
  if (action === 'left') return list.slice(0, i).map((t) => t.path)
  if (action === 'right') return list.slice(i + 1).map((t) => t.path)
  if (action === 'others') return list.filter((_, k) => k !== i).map((t) => t.path)
  return []
}

export function setDirty(tabs, path, dirty) {
  const list = tabs || []
  const i = list.findIndex((t) => t.path === path)
  if (i === -1) return list
  const next = list.slice()
  next[i] = { ...next[i], dirty: !!dirty }
  return next
}

export function hasDirty(tabs) {
  return (tabs || []).some((t) => t.dirty)
}

function cleanPaths(tabs) {
  const out = []
  for (const t of tabs || []) {
    const p = t && t.path
    if (typeof p === 'string' && p && !out.includes(p)) out.push(p)
  }
  return out
}

export function encodeTabs(tabs, active) {
  const paths = cleanPaths(tabs)
  return JSON.stringify({ tabs: paths, active: paths.includes(active) ? active : (paths[0] || '') })
}

export function decodeTabs(raw) {
  const empty = { tabs: [], active: '' }
  if (typeof raw !== 'string' || !raw) return empty
  let data
  try {
    data = JSON.parse(raw)
  } catch {
    return empty
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return empty
  if (!Array.isArray(data.tabs)) return empty
  const paths = cleanPaths(data.tabs.map((path) => ({ path })))
  return {
    tabs: paths.map((path) => ({ path, dirty: false })),
    active: typeof data.active === 'string' && paths.includes(data.active) ? data.active : (paths[0] || ''),
  }
}
