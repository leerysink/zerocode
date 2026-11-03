// 应用向导表单规则（与后端 slugs.py / app_service._check_scale_stack 对齐）
export const RE = /^[a-z][a-z0-9]{1,39}$/
export const SMALL_STACKS = [
  { value: 'vue3-fastapi', label: 'Vue3 + FastAPI（Python）' },
  { value: 'vue3-node', label: 'Vue3 + Node（Express）' },
  { value: 'static', label: '纯前端静态页' },
]
// 大型档栈名单：移动栈只是 springboot 底座上多渲染一份 uni-app 工程（ADR-0039），
// 目录、端口与发布流程同形，所以并排放在同一档里选。
export const LARGE_STACKS = [
  { value: 'vue3-springboot', label: 'Vue3 + Spring Boot 3（默认）' },
  { value: 'vue3-springboot-mobile', label: 'Vue3 + Spring Boot 3 + uni-app 移动端' },
]

const inStacks = (list, stack) => list.some((s) => s.value === stack)

export function slugFromName(name) {
  return (name || '').toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 40)
}

export function validateForm({ name, slug, scale, stack }) {
  if (!name || !name.trim()) return { ok: false, msg: '请填写应用名称' }
  if (!RE.test(slug || '')) return { ok: false, msg: 'slug 需以小写字母开头，仅含小写字母/数字（2-40 位）' }
  if (!stack) return { ok: false, msg: '请选择技术栈' }
  if (scale === 'large' && !inStacks(LARGE_STACKS, stack))
    return { ok: false, msg: '大型应用锁定 Vue3 + Spring Boot 3 底座' }
  // 反方向也要守（与后端 _check_scale_stack 对齐）：small + 移动栈会渲染大型栈目录，
  // 却在 scale 列写着 small，两边语义打架。
  if (scale === 'small' && !inStacks(SMALL_STACKS, stack))
    return { ok: false, msg: '该技术栈不属于小型档' }
  return { ok: true, msg: '' }
}
