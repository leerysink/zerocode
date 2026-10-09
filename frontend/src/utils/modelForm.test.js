import { describe, it, expect } from 'vitest'
import {
  API_KEY_MASK, DEFAULT_MODEL_KIND, emptyModelForm, modelKindLabel,
  validateModelForm, buildModelPayload,
} from './modelForm'

describe('emptyModelForm', () => {
  it('返回全空的新建表单（类型默认 chat）', () => {
    expect(emptyModelForm()).toEqual({
      name: '',
      base_url: '',
      api_key: '',
      model: '',
      kind: 'chat',
      is_default: false,
    })
  })
})

describe('validateModelForm', () => {
  const ok = {
    name: 'DeepSeek 官方',
    base_url: 'https://api.deepseek.com/v1',
    api_key: 'sk-x',
    model: 'deepseek-chat',
    kind: 'chat',
    is_default: false,
  }

  it('新建资料齐全返回 null', () => {
    expect(validateModelForm(ok, false)).toBeNull()
  })

  it('编辑资料齐全（api_key 留空合法）返回 null', () => {
    expect(validateModelForm({ ...ok, api_key: '' }, true)).toBeNull()
  })

  it('新建缺 api_key 报错', () => {
    expect(validateModelForm({ ...ok, api_key: '' }, false)).toBe('请填写 API Key')
  })

  it('空白字符视为未填，按 名称→Base URL→模型名→API Key 顺序报首个错误', () => {
    expect(validateModelForm({ ...ok, name: '  ' }, false)).toBe('请填写名称')
    expect(validateModelForm({ ...ok, base_url: ' ' }, false)).toBe('请填写 Base URL')
    expect(validateModelForm({ ...ok, model: '\t' }, false)).toBe('请填写模型名')
    expect(validateModelForm({ ...ok, api_key: ' ' }, false)).toBe('请填写 API Key')
  })

  // ---- kind 边界（Ticket 005）----
  it('embedding 是合法类型', () => {
    expect(validateModelForm({ ...ok, kind: 'embedding' }, false)).toBeNull()
  })

  it('kind 缺失视为未选，编辑态也照样拦（缺值提交会把已有向量档案刷回 chat）', () => {
    const { kind, ...noKind } = ok
    expect(validateModelForm(noKind, true)).toBe('请选择模型类型')
    expect(validateModelForm({ ...ok, kind: '' }, false)).toBe('请选择模型类型')
    expect(validateModelForm({ ...ok, kind: null }, false)).toBe('请选择模型类型')
  })

  it('kind 拼错（如 vl）不静默接受', () => {
    expect(validateModelForm({ ...ok, kind: 'vl' }, false)).toBe('请选择模型类型')
  })

  it('其他字段已不合法时先报其他字段（kind 排在末位）', () => {
    expect(validateModelForm({ ...ok, name: '', kind: 'vl' }, false)).toBe('请填写名称')
  })
})

describe('buildModelPayload', () => {
  const form = {
    name: 'D',
    base_url: 'https://x/v1',
    api_key: 'sk-new',
    model: 'm',
    kind: 'chat',
    is_default: true,
  }

  it('新建原样提交', () => {
    expect(buildModelPayload(form, false)).toEqual({
      name: 'D',
      base_url: 'https://x/v1',
      api_key: 'sk-new',
      model: 'm',
      kind: 'chat',
      is_default: true,
    })
  })

  it('编辑填了新 key 原样提交', () => {
    expect(buildModelPayload(form, true).api_key).toBe('sk-new')
  })

  it('编辑 api_key 留空填掩码哨兵（后端保留原密钥）', () => {
    const payload = buildModelPayload({ ...form, api_key: '' }, true)
    expect(payload.api_key).toBe(API_KEY_MASK)
    expect(payload.api_key).toBe('sk-***')
  })

  it('新建 api_key 留空不填哨兵（由校验前置拦截）', () => {
    expect(buildModelPayload({ ...form, api_key: '' }, false).api_key).toBe('')
  })

  it('is_default 归一为布尔', () => {
    expect(buildModelPayload({ ...form, is_default: 1 }, false).is_default).toBe(true)
    expect(buildModelPayload({ ...form, is_default: 0 }, false).is_default).toBe(false)
  })

  it('文本字段统一 trim（与校验口径一致，防空白入库）', () => {
    const payload = buildModelPayload({ ...form, name: '  D ', base_url: ' https://x/v1 ', model: ' m ' }, false)
    expect(payload.name).toBe('D')
    expect(payload.base_url).toBe('https://x/v1')
    expect(payload.model).toBe('m')
  })

  // ---- kind 边界（Ticket 005）----
  it('embedding 原样提交', () => {
    expect(buildModelPayload({ ...form, kind: 'embedding' }, false).kind).toBe('embedding')
  })

  it('kind 缺失或非法时兜底 chat（校验层已拦下，这里只是不往载荷里送 undefined）', () => {
    expect(buildModelPayload({ ...form, kind: undefined }, false).kind).toBe(DEFAULT_MODEL_KIND)
    expect(buildModelPayload({ ...form, kind: 'VL' }, true).kind).toBe(DEFAULT_MODEL_KIND)
  })
})

describe('modelKindLabel', () => {
  it('已知类型给中文标题', () => {
    expect(modelKindLabel('chat')).toBe('对话模型')
    expect(modelKindLabel('embedding')).toBe('向量模型（记忆召回）')
  })

  it('未知类型原样回显、空值不报错（不能把列表渲染成空白）', () => {
    expect(modelKindLabel('vl')).toBe('vl')
    expect(modelKindLabel('')).toBe('')
    expect(modelKindLabel(null)).toBe('')
    expect(modelKindLabel(undefined)).toBe('')
  })
})
