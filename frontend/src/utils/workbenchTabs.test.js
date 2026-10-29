import { describe, it, expect } from 'vitest'
import {
  closeTab, decodeTabs, encodeTabs, getTab, hasDirty, neighborAfterClose,
  openTab, setDirty, tabsStorageKey,
} from './workbenchTabs'

const tabsOf = (paths) => paths.map((path) => ({ path, dirty: false }))
const paths = (tabs) => tabs.map((t) => t.path)

describe('openTab 打开与去重', () => {
  it('新路径追加到末尾并激活', () => {
    const r = openTab([], 'a.md')
    expect(paths(r.tabs)).toEqual(['a.md'])
    expect(r.active).toBe('a.md')
    expect(r.tabs[0].dirty).toBe(false)
  })

  it('已存在的路径只激活，不重复不入脏', () => {
    const tabs = tabsOf(['a.md', 'b.md'])
    const r = openTab(tabs, 'a.md')
    expect(paths(r.tabs)).toEqual(['a.md', 'b.md'])
    expect(r.active).toBe('a.md')
    expect(r.tabs[0].dirty).toBe(false)
  })
})

describe('setDirty', () => {
  it('只改目标页签，不原地改传入数组', () => {
    const tabs = tabsOf(['a.md', 'b.md'])
    const next = setDirty(tabs, 'b.md', true)
    expect(getTab(next, 'b.md').dirty).toBe(true)
    expect(getTab(next, 'a.md').dirty).toBe(false)
    expect(tabs[1].dirty).toBe(false)
  })

  it('未知路径原样返回', () => {
    const tabs = tabsOf(['a.md'])
    expect(setDirty(tabs, 'nope.md', true)).toBe(tabs)
  })
})

describe('closeTab 与激活邻居', () => {
  it('关闭激活的中间页签 → 右侧邻居', () => {
    const tabs = tabsOf(['a.md', 'b.md', 'c.md'])
    expect(neighborAfterClose(tabs, 'b.md')).toBe('c.md')
    const r = closeTab(tabs, 'b.md', 'b.md')
    expect(paths(r.tabs)).toEqual(['a.md', 'c.md'])
    expect(r.active).toBe('c.md')
  })

  it('关闭激活的最后一个页签 → 左侧邻居', () => {
    const tabs = tabsOf(['a.md', 'b.md'])
    expect(neighborAfterClose(tabs, 'b.md')).toBe('a.md')
    const r = closeTab(tabs, 'b.md', 'b.md')
    expect(paths(r.tabs)).toEqual(['a.md'])
    expect(r.active).toBe('a.md')
  })

  it('关闭非激活页签 → 激活项不变', () => {
    const tabs = tabsOf(['a.md', 'b.md'])
    const r = closeTab(tabs, 'a.md', 'b.md')
    expect(paths(r.tabs)).toEqual(['b.md'])
    expect(r.active).toBe('b.md')
  })

  it('关闭最后一个页签 → 空列表与空激活项', () => {
    const r = closeTab(tabsOf(['a.md']), 'a.md', 'a.md')
    expect(r.tabs).toEqual([])
    expect(r.active).toBe('')
    expect(neighborAfterClose(tabsOf(['a.md']), 'a.md')).toBeNull()
  })
})

describe('hasDirty', () => {
  it('任一页签脏即为真', () => {
    expect(hasDirty(tabsOf(['a.md']))).toBe(false)
    expect(hasDirty([{ path: 'a.md', dirty: true }])).toBe(true)
    expect(hasDirty([])).toBe(false)
  })
})

describe('编解码与存储键', () => {
  it('圆桌：只存路径与激活项，不存脏态', () => {
    const raw = encodeTabs([{ path: 'a.md', dirty: true }], 'a.md')
    const r = decodeTabs(raw)
    expect(paths(r.tabs)).toEqual(['a.md'])
    expect(r.tabs[0].dirty).toBe(false)
    expect(r.active).toBe('a.md')
  })

  it('去重、过滤空串与非字符串，激活项不在列表时退回第一项', () => {
    const r = decodeTabs(JSON.stringify({ tabs: ['a.md', 'a.md', '', 7, null, 'b.md'], active: 'zzz' }))
    expect(paths(r.tabs)).toEqual(['a.md', 'b.md'])
    expect(r.active).toBe('a.md')
  })

  it('坏 JSON / 非对象 / tabs 非数组 / null 一律空列表', () => {
    for (const raw of ['{', '[]', '{"tabs":"a.md"}', '{"tabs":null}', null, undefined]) {
      const r = decodeTabs(raw)
      expect(r.tabs).toEqual([])
      expect(r.active).toBe('')
    }
  })

  it('存储键按工作区隔离且带版本号', () => {
    expect(tabsStorageKey(7)).toBe('zc.workbench.tabs.v1.7')
    expect(tabsStorageKey('2026092201000900123')).toBe('zc.workbench.tabs.v1.2026092201000900123')
  })
})
