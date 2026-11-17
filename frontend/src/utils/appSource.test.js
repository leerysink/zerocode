// 向导载荷里的「仓库地址」段：空/空白 → 整个字段缺席（不许把空格变成一次网络请求）；
// 超长 → 拒（后端 URL 上限 1000，前端先拦一次，给用户一句能看懂的话）
import { describe, expect, it } from 'vitest'
import { MAX_SOURCE_URL, sourcePayload } from './appSource'

describe('sourcePayload', () => {
  it('空值不产出字段', () => {
    expect(sourcePayload('')).toBeUndefined()
    expect(sourcePayload('   ')).toBeUndefined()
    expect(sourcePayload(undefined)).toBeUndefined()
    expect(sourcePayload(null)).toBeUndefined()
  })

  it('纯地址带 url，已去首尾空白', () => {
    // toStrictEqual 而非 toEqual：toEqual 会放过 {url, subpath: undefined}，
    // 「键根本不存在」这件事必须由 toStrictEqual 来说
    expect(sourcePayload('  https://gitee.com/o/r  ')).toStrictEqual({
      url: 'https://gitee.com/o/r',
    })
  })

  it('子目录空串不落字段（后端把空串读成「仓库根」是有意的，缺省才是「全域扫描」）', () => {
    expect(sourcePayload('https://gitee.com/o/r', '')).toStrictEqual({ url: 'https://gitee.com/o/r' })
    // 全空白的子目录同空串：不能让 .trim() 一漏就把 '   ' 当成「显式表态」发出去。
    // 理由不是「后端形态闸会回一句看不懂的 400」（Task 10 之后抓取侧先整体 strip 空白，
    // '   ' 归一成仓库根，那道闸根本不触发），而是 US-08：发出 subpath 键 = 用户显式说
    // 「就要仓库根」，它会压过 URL 里带的 /tree/<ref>/<目录>，与刚粘地址的人的意图正相反。
    // 空白框是「没表态」，所以键必须缺席。
    expect(sourcePayload('https://gitee.com/o/r', '   '))
      .toStrictEqual({ url: 'https://gitee.com/o/r' })
    expect(sourcePayload('https://gitee.com/o/r', '  docs/adr ')).toStrictEqual({
      url: 'https://gitee.com/o/r', subpath: 'docs/adr',
    })
  })

  it('超长直接报错而不是发出去吃 400', () => {
    expect(() => sourcePayload('https://gitee.com/o/' + 'r'.repeat(MAX_SOURCE_URL)))
      .toThrow(/上限/)
  })
})
