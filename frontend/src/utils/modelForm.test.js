import { describe, it, expect } from 'vitest'
import { API_KEY_MASK, emptyModelForm, validateModelForm, buildModelPayload } from './modelForm'

describe('emptyModelForm', () => {
  it('返回全空的新建表单', () => {
    expect(emptyModelForm()).toEqual({
      name: '',
      base_url: '',
      api_key: '',
      model: '',
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
})

describe('buildModelPayload', () => {
  const form = {
    name: 'D',
    base_url: 'https://x/v1',
    api_key: 'sk-new',
    model: 'm',
    is_default: true,
  }

  it('新建原样提交', () => {
    expect(buildModelPayload(form, false)).toEqual({
      name: 'D',
      base_url: 'https://x/v1',
      api_key: 'sk-new',
      model: 'm',
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
})
