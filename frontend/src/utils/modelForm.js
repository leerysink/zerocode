// 模型库表单纯函数（Task 16）：表单初值 / 校验 / 提交载荷。
// api_key 哨兵契约（后端 app/api/models.py）：PUT 传掩码 "sk-***" 表示保留原密钥；
// 编辑表单留空 = 不改，提交时由 buildModelPayload 填哨兵；新建必填由 validateModelForm 拦截。
export const API_KEY_MASK = 'sk-***'

// 模型类型（Ticket 005 / D-2）：与后端 app/api/models.py 的 MODEL_KINDS 一一对应。
// 初值定 chat：默认给 embedding 会让对话模型下拉（只取 chat）在大多数部署下直接空掉。
export const MODEL_KIND_OPTIONS = [
  { value: 'chat', label: '对话模型' },
  { value: 'embedding', label: '向量模型（记忆召回）' },
]
export const DEFAULT_MODEL_KIND = 'chat'
const KIND_VALUES = MODEL_KIND_OPTIONS.map((o) => o.value)

// 列表回显用的标题：未识别的类型（存量脏数据或以后新增的分类）原样显示，不能渲染成空白。
export function modelKindLabel(kind) {
  return MODEL_KIND_OPTIONS.find((o) => o.value === kind)?.label ?? String(kind ?? '')
}

export function emptyModelForm() {
  return { name: '', base_url: '', api_key: '', model: '', kind: DEFAULT_MODEL_KIND, is_default: false }
}

// 返回首个错误的文案，全部合法返回 null。isEdit 时 api_key 允许留空（=保留原密钥）。
// 类型放在末位校验：既有字段的报错顺序不能动（单测按 名称→Base URL→模型名→API Key 断言），
// 且类型在表单上由下拉给出、正常不会空；拦住它是为了防止「缺 kind 提交」把已有向量档案刷回 chat。
export function validateModelForm(form, isEdit) {
  if (!String(form.name || '').trim()) return '请填写名称'
  if (!String(form.base_url || '').trim()) return '请填写 Base URL'
  if (!String(form.model || '').trim()) return '请填写模型名'
  if (!isEdit && !String(form.api_key || '').trim()) return '请填写 API Key'
  if (!KIND_VALUES.includes(form.kind)) return '请选择模型类型'
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
    kind: KIND_VALUES.includes(form.kind) ? form.kind : DEFAULT_MODEL_KIND,
    is_default: !!form.is_default,
  }
}
