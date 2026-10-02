// source_type → 中文短标签：url→直链、github→GitHub、ai→AI 生成。
// 未知值原样显示（未来新增类型不必改前端）；空/null 显示 '-'。
// Object.create(null) 防 constructor/toString 这类原型链属性被误当成命中。
const LABELS = Object.assign(Object.create(null), {
  url: '直链',
  github: 'GitHub',
  ai: 'AI 生成',
})

export function skillSourceLabel(sourceType) {
  const key = typeof sourceType === 'string' ? sourceType.trim() : ''
  if (!key) return '-'
  return LABELS[key] ?? key
}
