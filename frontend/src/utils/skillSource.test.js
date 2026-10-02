// source_type → 中文标签的纯函数测试（与后端 user_skill.source_type 取值对齐）。
import { describe, expect, it } from 'vitest'
import { skillSourceLabel } from './skillSource'

describe('skillSourceLabel', () => {
  it('url → 直链', () => {
    expect(skillSourceLabel('url')).toBe('直链')
  })

  it('github → GitHub', () => {
    expect(skillSourceLabel('github')).toBe('GitHub')
  })

  it('ai → AI 生成', () => {
    expect(skillSourceLabel('ai')).toBe('AI 生成')
  })

  it('未知值原样显示（未来新增类型不必改前端）', () => {
    expect(skillSourceLabel('future-type')).toBe('future-type')
  })

  it('空串显示 -', () => {
    expect(skillSourceLabel('')).toBe('-')
  })

  it('null/undefined 显示 -', () => {
    expect(skillSourceLabel(null)).toBe('-')
    expect(skillSourceLabel(undefined)).toBe('-')
  })

  it('前后空格不影响匹配', () => {
    expect(skillSourceLabel('  ai  ')).toBe('AI 生成')
  })

  it('非字符串入参不抛错，按空处理', () => {
    expect(skillSourceLabel(0)).toBe('-')
    expect(skillSourceLabel({})).toBe('-')
  })

  it('原型链属性不命中（防 constructor/toString 误匹配）', () => {
    expect(skillSourceLabel('constructor')).toBe('constructor')
    expect(skillSourceLabel('toString')).toBe('toString')
  })
})
