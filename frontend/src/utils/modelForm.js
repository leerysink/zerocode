// 模型库表单纯函数（Task 16）：表单初值 / 校验 / 提交载荷。
// api_key 哨兵契约（后端 app/api/models.py）：PUT 传掩码 "sk-***" 表示保留原密钥；
// 编辑表单留空 = 不改，提交时由 buildModelPayload 填哨兵；新建必填由 validateModelForm 拦截。
export const API_KEY_MASK = 'sk-***'

export function emptyModelForm() {
  return { name: '', base_url: '', api_key: '', model: '', is_default: false }
}

// 返回首个错误的文案，全部合法返回 null。isEdit 时 api_key 允许留空（=保留原密钥）。
export function validateModelForm(form, isEdit) {
  if (!String(form.name || '').trim()) return '请填写名称'
  if (!String(form.base_url || '').trim()) return '请填写 Base URL'
  if (!String(form.model || '').trim()) return '请填写模型名'
  if (!isEdit && !String(form.api_key || '').trim()) return '请填写 API Key'
  return null
}

// 组装 POST/PUT 载荷：仅编辑且 api_key 留空时填掩码哨兵；文本字段统一 trim（与校验口径一致，防空白入库）。
export function buildModelPayload(form, isEdit) {
  const editKeyEmpty = isEdit && !String(form.api_key || '').trim()
  return {
    name: String(form.name || '').trim(),
    base_url: String(form.base_url || '').trim(),
    api_key: editKeyEmpty ? API_KEY_MASK : String(form.api_key || '').trim(),
    model: String(form.model || '').trim(),
    is_default: !!form.is_default,
  }
}
