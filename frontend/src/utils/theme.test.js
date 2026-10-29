import { describe, it, expect } from 'vitest'
import {
  DEFAULT_THEME, THEME_KEY, nextTheme, normalizeTheme, readWorkbenchTheme, writeWorkbenchTheme,
} from './theme'

function fakeStorage(initial = {}) {
  const data = { ...initial }
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v) },
    data,
  }
}

describe('工作台主题', () => {
  it('缺省与坏值一律深色', () => {
    expect(DEFAULT_THEME).toBe('dark')
    for (const v of [undefined, null, '', 'DARK', 'light ']) {
      expect(normalizeTheme(v)).toBe('dark')
    }
    expect(normalizeTheme('light')).toBe('light')
  })

  it('读写走固定键，坏值读出深色', () => {
    const storage = fakeStorage({ [THEME_KEY]: 'light' })
    expect(THEME_KEY).toBe('zc.workbench.theme.v1')
    expect(readWorkbenchTheme(storage)).toBe('light')
    expect(writeWorkbenchTheme('light', storage)).toBe(true)
    expect(storage.data[THEME_KEY]).toBe('light')
    const bad = fakeStorage({ [THEME_KEY]: 'nope' })
    expect(readWorkbenchTheme(bad)).toBe('dark')
  })

  it('存储不可用时不抛错', () => {
    const broken = { getItem() { throw new Error('quota') }, setItem() { throw new Error('quota') } }
    expect(readWorkbenchTheme(broken)).toBe('dark')
    expect(writeWorkbenchTheme('light', broken)).toBe(false)
  })

  it('nextTheme 在深浅之间翻转', () => {
    expect(nextTheme('dark')).toBe('light')
    expect(nextTheme('light')).toBe('dark')
  })
})
