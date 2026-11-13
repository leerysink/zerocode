// 指标读数格式化（仪表卡与资源监控弹框共用）：口径只在这一处，两处显示必须同进同退。
import { describe, expect, it } from 'vitest'
import { fmtBytes, fmtPct } from './metricsFormat'

describe('fmtBytes', () => {
  it('空值返 null，不伪装成 0 字节', () => {
    expect(fmtBytes(null)).toBeNull()
    expect(fmtBytes(undefined)).toBeNull()
  })
  it('字节档不带小数', () => {
    expect(fmtBytes(0)).toBe('0 B')
    expect(fmtBytes(1023)).toBe('1023 B')
  })
  it('跨过 1024 换档并留一位小数', () => {
    expect(fmtBytes(1024)).toBe('1.0 KB')
    expect(fmtBytes(1234567)).toBe('1.2 MB')
    expect(fmtBytes(5 * 1024 ** 3)).toBe('5.0 GB')
    expect(fmtBytes(3 * 1024 ** 4)).toBe('3.0 TB')
  })
  it('字符串数字照收（后端 DECIMAL 可能序列化成串）', () => {
    expect(fmtBytes('2048')).toBe('2.0 KB')
  })
})

describe('fmtPct', () => {
  it('空值返 null，绝不画成 0.0 %', () => {
    expect(fmtPct(null)).toBeNull()
    expect(fmtPct(undefined)).toBeNull()
  })
  it('固定一位小数', () => {
    expect(fmtPct(15)).toBe('15.0 %')
    expect(fmtPct(156.25)).toBe('156.3 %')
  })
})
