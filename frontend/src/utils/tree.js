// 文件树 → 文件平铺列表（@补全数据源）
export function flattenTree(nodes, out = []) {
  for (const n of nodes || []) {
    if (n.type === 'file') out.push({ path: n.path, name: n.name })
    if (n.children) flattenTree(n.children, out)
  }
  return out
}
