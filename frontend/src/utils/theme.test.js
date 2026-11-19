import { describe, it, expect } from 'vitest'
import {
  THEME_KEY, DEFAULT_CONSOLE_THEME, CONSOLE_THEME_KEY, normalizeConsoleTheme, readConsoleTheme,
  writeConsoleTheme, nextConsoleTheme,
} from './theme'

function fakeStorage(initial = {}) {
  const data = { ...initial }
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v) },
    data,
  }
}

// 统一管辖后只剩控制台主题一套口径（ADR-0055）：旧工作台「缺省深色」系列断言
// 已镜像改写为下面的「缺省浅色」系列；旧键只作为继承来源被读，永不写。
describe('控制台主题', () => {
  it('缺省与坏值一律浅色，只认精确 dark', () => {
    expect(DEFAULT_CONSOLE_THEME).toBe('light')
    for (const v of [undefined, null, '', 'DARK', 'dark ', 'nope']) {
      expect(normalizeConsoleTheme(v)).toBe('light')
    }
    expect(normalizeConsoleTheme('dark')).toBe('dark')
  })

  it('读新键：合法值原样，坏值与空仓浅色', () => {
    expect(CONSOLE_THEME_KEY).toBe('zc.console.theme.v1')
    expect(readConsoleTheme(fakeStorage({ [CONSOLE_THEME_KEY]: 'dark' }))).toBe('dark')
    expect(readConsoleTheme(fakeStorage({ [CONSOLE_THEME_KEY]: 'light' }))).toBe('light')
    expect(readConsoleTheme(fakeStorage({ [CONSOLE_THEME_KEY]: 'nope' }))).toBe('light')
    expect(readConsoleTheme(fakeStorage())).toBe('light')
  })

  it('旧键一次性继承：新键缺席且旧键合法显式值才继承并写新键', () => {
    const inheritDark = fakeStorage({ [THEME_KEY]: 'dark' })
    expect(readConsoleTheme(inheritDark)).toBe('dark')
    expect(inheritDark.data[CONSOLE_THEME_KEY]).toBe('dark')

    const inheritLight = fakeStorage({ [THEME_KEY]: 'light' })
    expect(readConsoleTheme(inheritLight)).toBe('light')
    expect(inheritLight.data[CONSOLE_THEME_KEY]).toBe('light')

    const legacyDirty = fakeStorage({ [THEME_KEY]: 'nope' })
    expect(readConsoleTheme(legacyDirty)).toBe('light')
    expect(CONSOLE_THEME_KEY in legacyDirty.data).toBe(false)

    const bothAbsent = fakeStorage()
    expect(readConsoleTheme(bothAbsent)).toBe('light')
    expect(CONSOLE_THEME_KEY in bothAbsent.data).toBe(false)

    const newKeyWins = fakeStorage({ [CONSOLE_THEME_KEY]: 'nope', [THEME_KEY]: 'dark' })
    expect(readConsoleTheme(newKeyWins)).toBe('light')
    expect(newKeyWins.data[CONSOLE_THEME_KEY]).toBe('nope')
  })

  it('写入归一化后落新键；存储不可用不抛错', () => {
    const storage = fakeStorage()
    expect(writeConsoleTheme('dark', storage)).toBe(true)
    expect(storage.data[CONSOLE_THEME_KEY]).toBe('dark')
    expect(writeConsoleTheme('LIGHT', storage)).toBe(true)
    expect(storage.data[CONSOLE_THEME_KEY]).toBe('light')
    const broken = { getItem() { throw new Error('quota') }, setItem() { throw new Error('quota') } }
    expect(writeConsoleTheme('dark', broken)).toBe(false)
    expect(readConsoleTheme(broken)).toBe('light')
  })

  it('nextConsoleTheme 在深浅之间翻转', () => {
    expect(nextConsoleTheme('light')).toBe('dark')
    expect(nextConsoleTheme('dark')).toBe('light')
    expect(nextConsoleTheme('nope')).toBe('dark')
  })
})