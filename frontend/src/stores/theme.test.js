// @vitest-environment happy-dom
// theme store 契约：初始值来自存储读数（含旧键继承），toggle 翻转并落盘 + 落到 <html data-theme>，
// storage 事件只认新键。localStorage 用 happy-dom 真实实现，每例清空。
import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { CONSOLE_THEME_KEY, THEME_KEY } from '../utils/theme'
import { useThemeStore } from './theme'

describe('theme store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    document.documentElement.removeAttribute('data-theme')
  })

  it('初始浅色；toggle 翻深色并写存储与 html，再翻回浅色', () => {
    const store = useThemeStore()
    expect(store.theme).toBe('light')
    store.toggle()
    expect(store.theme).toBe('dark')
    expect(localStorage.getItem(CONSOLE_THEME_KEY)).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    store.toggle()
    expect(store.theme).toBe('light')
    expect(localStorage.getItem(CONSOLE_THEME_KEY)).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('初始值尊重存储：新键深色直接深色，旧键显式值经继承生效', () => {
    localStorage.setItem(CONSOLE_THEME_KEY, 'dark')
    expect(useThemeStore().theme).toBe('dark')

    localStorage.clear()
    setActivePinia(createPinia())
    localStorage.setItem(THEME_KEY, 'dark')
    const store = useThemeStore()
    expect(store.theme).toBe('dark')
    expect(localStorage.getItem(CONSOLE_THEME_KEY)).toBe('dark')
  })

  it('storage 事件只认新键：同步状态与 html，旧键与他键不响应', () => {
    const store = useThemeStore()
    store.syncFromStorage({ key: CONSOLE_THEME_KEY, newValue: 'dark' })
    expect(store.theme).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem(CONSOLE_THEME_KEY)).toBe(null) // 同步只读不回写

    store.syncFromStorage({ key: THEME_KEY, newValue: 'light' })
    expect(store.theme).toBe('dark')
    store.syncFromStorage({ key: 'other', newValue: 'light' })
    expect(store.theme).toBe('dark')

    store.syncFromStorage({ key: CONSOLE_THEME_KEY, newValue: null })
    expect(store.theme).toBe('light')
  })
})