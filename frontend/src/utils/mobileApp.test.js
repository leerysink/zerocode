// 移动栈判定（卡面 ☰ 与聊天页「更多」共用）：只认后端登记的 stack key。
import { describe, expect, it } from 'vitest'
import { MOBILE_STACK, isMobileApp } from './mobileApp'

describe('isMobileApp', () => {
  it('移动栈为真', () => {
    expect(isMobileApp({ stack: MOBILE_STACK })).toBe(true)
  })
  it('其他栈与空值一律为假', () => {
    expect(isMobileApp({ stack: 'vue3-springboot' })).toBe(false)
    expect(isMobileApp({ stack: 'vue3-fastapi' })).toBe(false)
    expect(isMobileApp(null)).toBe(false)
    expect(isMobileApp(undefined)).toBe(false)
    expect(isMobileApp({})).toBe(false)
  })
})
