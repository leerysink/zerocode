// 应用向导表单校验纯函数测试（与后端 slugs.py / app_service._check_scale_stack 规则对齐）
import { describe, expect, it } from 'vitest'
import { slugFromName, validateForm } from './appForm'

describe('slugFromName', () => {
  it('提取小写字母数字', () => {
    expect(slugFromName('My App2')).toBe('myapp2')
  })
  it('全中文时返回空串', () => {
    expect(slugFromName('来料登记')).toBe('')
  })
})

describe('validateForm', () => {
  const base = { name: '来料登记', slug: 'lailiao', scale: 'small', stack: 'vue3-fastapi' }
  it('合法通过', () => {
    expect(validateForm(base).ok).toBe(true)
  })
  it('slug 非法', () => {
    expect(validateForm({ ...base, slug: '2ab' }).ok).toBe(false)
  })
  it('large 锁定 springboot 两档（含 uni-app 移动端叠加栈）', () => {
    expect(validateForm({ ...base, scale: 'large', stack: 'vue3-fastapi' }).ok).toBe(false)
    expect(validateForm({ ...base, scale: 'large', stack: 'vue3-springboot' }).ok).toBe(true)
    expect(validateForm({ ...base, scale: 'large', stack: 'vue3-springboot-mobile' }).ok).toBe(true)
  })
  it('反方向：small 不得选大型档栈（移动栈/springboot）', () => {
    expect(validateForm({ ...base, stack: 'vue3-springboot-mobile' }).ok).toBe(false)
    expect(validateForm({ ...base, stack: 'vue3-springboot' }).ok).toBe(false)
  })
  it('未选栈优先提示选择，而不是档位锁定信息', () => {
    expect(validateForm({ ...base, scale: 'large', stack: '' }).msg).toBe('请选择技术栈')
  })
})
