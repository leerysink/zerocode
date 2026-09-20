// 应用向导表单规则（与后端 slugs.py / app_service._check_scale_stack 对齐）
export const RE = /^[a-z][a-z0-9]{1,39}$/
export const SMALL_STACKS = [
  { value: 'vue3-fastapi', label: 'Vue3 + FastAPI（Python）' },
  { value: 'vue3-node', label: 'Vue3 + Node（Express）' },
  { value: 'static', label: '纯前端静态页' },
]

export function slugFromName(name) {
  return (name || '').toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 40)
}

export function validateForm({ name, slug, scale, stack }) {
  if (!name || !name.trim()) return { ok: false, msg: '请填写应用名称' }
  if (!RE.test(slug || '')) return { ok: false, msg: 'slug 需以小写字母开头，仅含小写字母/数字（2-40 位）' }
  if (scale === 'large' && stack !== 'vue3-springboot')
    return { ok: false, msg: '大型应用锁定 Vue3 + Spring Boot 3' }
  if (!stack) return { ok: false, msg: '请选择技术栈' }
  return { ok: true, msg: '' }
}
