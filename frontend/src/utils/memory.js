// 记忆类型的中文标签与分组：面板（MemoryPanel）与记忆详情整页的左栏共用。
// 两处各写一份迟早会漂移（面板多一组、详情少一组），所以类型顺序与兜底规则只在这里定义：
// preference / decision / fact / pitfall 按固定顺序；未知 type 兜底归「其他」，不丢行。
export const TYPE_LABELS = {
  preference: '偏好',
  decision: '决策',
  fact: '事实',
  pitfall: '踩坑',
}

export function groupMemories(memories) {
  const list = Array.isArray(memories) ? memories : []
  const known = new Set(Object.keys(TYPE_LABELS))
  const groups = []
  for (const type of Object.keys(TYPE_LABELS)) {
    const items = list.filter((m) => m.type === type)
    if (items.length) groups.push({ type, label: TYPE_LABELS[type], items })
  }
  const extras = list.filter((m) => !known.has(m.type))
  if (extras.length) groups.push({ type: '_other', label: '其他', items: extras })
  return groups
}
