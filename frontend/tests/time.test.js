import { describe, it, expect } from 'vitest'
import { relativeTime } from '../src/utils/time'

describe('relativeTime', () => {
  it('刚刚 / 分钟 / 小时 / 天', () => {
    const now = Date.now()
    expect(relativeTime(new Date(now).toISOString(), now)).toBe('刚刚')
    expect(relativeTime(new Date(now - 61_000).toISOString(), now)).toBe('1分钟前')
    expect(relativeTime(new Date(now - 2 * 3600_000).toISOString(), now)).toBe('2小时前')
    expect(relativeTime(new Date(now - 3 * 86400_000).toISOString(), now)).toBe('3天前')
  })
  it('超过30天显示日期', () => {
    const now = Date.now()
    const old = new Date(now - 40 * 86400_000)
    expect(relativeTime(old.toISOString(), now)).toBe(old.toISOString().slice(0, 10))
  })
})
