import { describe, it, expect } from 'vitest'
import { activeMentionToken, applyMention } from './mentions'

describe('activeMentionToken', () => {
  it('光标前是 @词 时返回 token', () => {
    // 注：原 brief 写 caret=7（把 CJK「看」按 2 个 UTF-16 单位计数）；实长 6，按真实索引修正
    expect(activeMentionToken('看 @app', 6)).toEqual({ query: 'app', start: 2, end: 6 })
  })
  it('引号形式', () => {
    expect(activeMentionToken('@"src/a.ts"', 10).query).toBe('src/a.ts')
  })
  it('邮箱不触发（@ 前是单词字符）', () => {
    expect(activeMentionToken('hi@bar', 6)).toBeNull()
  })
  it('无 @ 返回 null', () => {
    expect(activeMentionToken('hello', 5)).toBeNull()
  })
})

describe('applyMention', () => {
  it('替换 @token 为带引号路径+空格', () => {
    const t = activeMentionToken('看 @app', 6)
    expect(applyMention('看 @app', t, 'src/app.js')).toBe('看 @"src/app.js" ')
  })
})
