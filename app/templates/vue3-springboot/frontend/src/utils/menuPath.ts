import type { MenuItem } from '@/store/user'

/**
 * 把 /auth/routers 下发的**扁平**菜单列表解析成 id → 完整路径。
 *
 * 后端两个分支（超管 / 按角色）都只查 menu_type in ('M','C') 且 status=1，
 * 按 parent_id + sort 排序后直接返回平铺数组，children 恒为空 —— 层级只靠 parentId 表达，
 * 子级的 path 是相对片段（如 'user'），必须拼上父级路径（如 '/system'）才对得上路由表。
 *
 * 侧边栏的 menu key 和路由守卫的允许集合都必须走这一个函数，
 * 否则两处各写一遍拼接逻辑，迟早漂移成「侧边栏点得进去但守卫判定越权」。
 */
export function buildMenuFullPath(menus: MenuItem[]): Map<number, string> {
  const byId = new Map<number, MenuItem>()
  menus.forEach((m) => byId.set(m.id, m))
  const fullPath = new Map<number, string>()

  function resolve(menu: MenuItem, depth: number): string {
    const cached = fullPath.get(menu.id)
    if (cached !== undefined) return cached
    // depth 上限兜住脏数据里的 parentId 成环，避免递归栈溢出
    const parent = menu.parentId && depth < 10 ? byId.get(menu.parentId) : undefined
    const result = joinPath(parent ? resolve(parent, depth + 1) : '', menu.path)
    fullPath.set(menu.id, result)
    return result
  }

  menus.forEach((m) => resolve(m, 0))
  return fullPath
}

/** 当前用户可见的完整路径集合，与路由表的 path 同一口径 */
export function collectMenuPaths(menus: MenuItem[]): string[] {
  return Array.from(buildMenuFullPath(menus).values()).filter(Boolean)
}

function joinPath(parent: string, child?: string): string {
  if (!child) return parent
  if (child.startsWith('/')) return child
  return `${parent}/${child}`
}
