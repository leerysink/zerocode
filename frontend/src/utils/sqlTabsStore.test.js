// Ticket 005：查询页签的本地持久化。
//
// 规格（5.4 / ADR 0008）：key = `zc.dbq.tabs.{appId}.{env}`，只存脚本不存结果；
// 无 key / JSON 坏 / version 不符 → 默认单页签不抛；上限 12；写失败 → quota 降级。
// 浏览器存储是不可靠的外部依赖，所以这里把 storage 做成注入点：测试用假对象，
// 组件里用真 localStorage，两种环境下契约一致。
import { describe, expect, it, vi } from 'vitest'
import {
  MAX_TABS,
  TABS_VERSION,
  addTab,
  createTabsState,
  loadTabs,
  removeTab,
  renameTab,
  saveTabs,
  tabsKey,
} from './sqlTabsStore'

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)) },
    removeItem: (k) => { map.delete(k) },
  }
}

const tab = (id, sql = '', name = id) => ({ id, name, sql })
const stateOf = (tabs, active = 0) => ({ version: TABS_VERSION, active, tabs })

describe('sqlTabsStore 主流程', () => {
  it('首次读（localStorage 里根本没有 key）返回默认单页签 query_1、空文本', () => {
    const s = loadTabs('1', 'dev', fakeStorage())
    expect(s).toEqual({
      version: TABS_VERSION,
      active: 0,
      tabs: [{ id: 'query_1', name: 'query_1', sql: '' }],
    })
  })

  it('写入后读回：页签名称、顺序、SQL 文本与激活索引原样恢复', () => {
    const st = fakeStorage()
    const input = stateOf([tab('query_1', 'SELECT 1'), tab('query_2', 'SELECT 2;\nSELECT 3'), tab('报表', '  select 4  ', '报表')], 2)
    expect(saveTabs('1', 'dev', input, st)).toEqual({ ok: true })
    expect(loadTabs('1', 'dev', st)).toEqual(input)
  })

  it('key 按 (appId, env) 分区，appId 数字与字符串拼出同一个键', () => {
    expect(tabsKey('1', 'dev')).toBe('zc.dbq.tabs.1.dev')
    expect(tabsKey(1, 'prod')).toBe('zc.dbq.tabs.1.prod')
    const st = fakeStorage()
    saveTabs(1, 'dev', stateOf([tab('query_1', 'SELECT 1')]), st)
    expect([...st.map.keys()]).toEqual(['zc.dbq.tabs.1.dev'])
  })

  it('空 SQL 页签也要落盘，读回来还是那个页签（不能当成空状态丢弃）', () => {
    const st = fakeStorage()
    saveTabs('1', 'dev', stateOf([tab('query_1', ''), tab('query_2', '')], 1), st)
    expect(loadTabs('1', 'dev', st)).toEqual(stateOf([tab('query_1', ''), tab('query_2', '')], 1))
  })

  it('只存脚本不存结果：运行时字段不进 localStorage，读回来只剩 id/name/sql', () => {
    const st = fakeStorage()
    const dirty = {
      version: TABS_VERSION,
      active: 0,
      tabs: [{ id: 'query_1', name: 'query_1', sql: 'SELECT 1', results: [{ status: 'ok' }], busy: true, extra: 'x' }],
      junk: 'should not persist',
    }
    saveTabs('1', 'dev', dirty, st)
    const raw = st.map.get('zc.dbq.tabs.1.dev')
    expect(raw).not.toContain('results')
    expect(raw).not.toContain('junk')
    const back = loadTabs('1', 'dev', st)
    expect(Object.keys(back)).toEqual(['version', 'active', 'tabs'])
    expect(Object.keys(back.tabs[0])).toEqual(['id', 'name', 'sql'])
  })

  it('addTab 用最小空闲编号命名，并把激活页签切到新建的这个', () => {
    const next = addTab(createTabsState())
    expect(next.active).toBe(1)
    expect(next.tabs).toHaveLength(2)
    expect(next.tabs[1]).toEqual({ id: 'query_2', name: 'query_2', sql: '' })
  })

  it('关掉 query_2 后再新建会复用 query_2 编号（不留空洞、不无限递增）', () => {
    const three = stateOf([tab('query_1', 'a'), tab('query_2', 'b'), tab('query_3', 'c')])
    const afterClose = removeTab(three, 'query_2')
    expect(afterClose.tabs.map((t) => t.id)).toEqual(['query_1', 'query_3'])
    expect(addTab(afterClose).tabs.map((t) => t.id)).toEqual(['query_1', 'query_3', 'query_2'])
  })

  it('renameTab 只改标题不改 id（id 是结果区与持久化的锚）', () => {
    const next = renameTab(stateOf([tab('query_1', 'SELECT 1')]), 'query_1', '月度对账')
    expect(next.tabs[0]).toEqual({ id: 'query_1', name: '月度对账', sql: 'SELECT 1' })
  })

  it('removeTab 关掉中间页签后 active 跟着左移，其余页签顺序不变', () => {
    const three = stateOf([tab('query_1', 'a'), tab('query_2', 'b'), tab('query_3', 'c')], 2)
    const next = removeTab(three, 'query_2')
    expect(next.tabs.map((t) => t.id)).toEqual(['query_1', 'query_3'])
    expect(next.active).toBe(1)
  })

  it('关掉唯一页签自动补一个空白页签——界面不允许出现 0 页签死状态', () => {
    const next = removeTab(stateOf([tab('query_1', 'SELECT 1')]), 'query_1')
    expect(next).toEqual(createTabsState())
  })
})

describe('sqlTabsStore 边界：损坏数据与降级', () => {
  it.each([
    ['截断的 JSON', '{"version":1,"tabs":[{"id":"query_1"'],
    ['空串', ''],
    ['字面量 null', 'null'],
    ['不是对象', '"abc"'],
    ['是数组', '[1,2]'],
  ])('边界：localStorage 里是%s → 回默认单页签，不抛异常', (_name, raw) => {
    const st = fakeStorage({ 'zc.dbq.tabs.1.dev': raw })
    expect(loadTabs('1', 'dev', st)).toEqual(createTabsState())
  })

  it.each([
    ['缺 version 字段', { active: 0, tabs: [tab('query_1', 'SELECT 1')] }],
    ['version 是未来版本', { version: TABS_VERSION + 1, active: 0, tabs: [tab('query_1', 'SELECT 1')] }],
    ['version 是老版本', { version: 0, active: 0, tabs: [tab('query_1', 'SELECT 1')] }],
    ['version 是字符串', { version: '1', active: 0, tabs: [tab('query_1', 'SELECT 1')] }],
  ])('边界：%s → 不做字段猜测，直接回默认单页签', (_name, obj) => {
    const st = fakeStorage({ 'zc.dbq.tabs.1.dev': JSON.stringify(obj) })
    expect(loadTabs('1', 'dev', st)).toEqual(createTabsState())
  })

  it.each([
    ['tabs 缺失', { version: TABS_VERSION, active: 0 }],
    ['tabs 不是数组', { version: TABS_VERSION, active: 0, tabs: 'query_1' }],
    ['tabs 是空数组', { version: TABS_VERSION, active: 0, tabs: [] }],
    ['tabs 全是不含 id 的空对象', { version: TABS_VERSION, active: 0, tabs: [{}, null, 'x'] }],
  ])('边界：%s → 默认单页签（宁可丢损坏条目也不让页签栏渲染出 undefined）', (_name, obj) => {
    const st = fakeStorage({ 'zc.dbq.tabs.1.dev': JSON.stringify(obj) })
    expect(loadTabs('1', 'dev', st)).toEqual(createTabsState())
  })

  it('边界：条目缺 name 用 id 兜、缺 sql 当空串、sql 是数字也当空串', () => {
    const st = fakeStorage({
      'zc.dbq.tabs.1.dev': JSON.stringify({
        version: TABS_VERSION,
        active: 1,
        tabs: [{ id: 'query_1' }, { id: 'query_2', name: '   ', sql: 123 }],
      }),
    })
    const s = loadTabs('1', 'dev', st)
    expect(s.tabs[0]).toEqual({ id: 'query_1', name: 'query_1', sql: '' })
    expect(s.tabs[1]).toEqual({ id: 'query_2', name: 'query_2', sql: '' })
  })

  it.each([
    ['存的是 5 但只剩 2 个页签', 5, 0],
    ['负数', -1, 0],
    ['非数字', 'x', 0],
    ['缺失', undefined, 0],
    ['正好等于长度（越界一位）', 2, 0],
  ])('边界：active %s → 回落 0，不能渲染成 tabs[undefined]', (_name, active, expectActive) => {
    const st = fakeStorage({
      'zc.dbq.tabs.1.dev': JSON.stringify({ version: TABS_VERSION, active, tabs: [tab('query_1', 'a'), tab('query_2', 'b')] }),
    })
    expect(loadTabs('1', 'dev', st).active).toBe(expectActive)
  })

  it('边界：读回超过上限的历史页签不丢用户脚本，只在新增时拒绝', () => {
    const many = Array.from({ length: MAX_TABS + 3 }, (_, i) => tab(`query_${i + 1}`, `SELECT ${i + 1}`))
    const st = fakeStorage({ 'zc.dbq.tabs.1.dev': JSON.stringify(stateOf(many)) })
    expect(loadTabs('1', 'dev', st).tabs).toHaveLength(MAX_TABS + 3)
    expect(addTab(loadTabs('1', 'dev', st))).toBeNull()
  })

  it('边界：localStorage 整个不可用（隐私模式 / 非浏览器）→ 读默认、写降级，不抛', () => {
    vi.stubGlobal('localStorage', undefined)
    try {
      expect(loadTabs('1', 'dev')).toEqual(createTabsState())
      expect(saveTabs('1', 'dev', createTabsState())).toEqual({ ok: false, reason: 'quota' })
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('sqlTabsStore 边界：页签上限', () => {
  it('第 12 个页签允许新建', () => {
    const eleven = stateOf(Array.from({ length: MAX_TABS - 1 }, (_, i) => tab(`query_${i + 1}`)))
    const next = addTab(eleven)
    expect(next.tabs).toHaveLength(MAX_TABS)
    expect(next.active).toBe(MAX_TABS - 1)
  })

  it('边界：第 13 个被拒（addTab 返回 null），且原 state 不被就地改动', () => {
    const twelve = stateOf(Array.from({ length: MAX_TABS }, (_, i) => tab(`query_${i + 1}`)))
    const snapshot = JSON.stringify(twelve)
    expect(addTab(twelve)).toBeNull()
    expect(JSON.stringify(twelve)).toBe(snapshot)
  })
})

describe('sqlTabsStore 边界：写入配额', () => {
  it('边界：setItem 抛 QuotaExceededError → {ok:false, reason:quota}（组件黄条的触发点）', () => {
    const st = fakeStorage()
    st.setItem = () => { throw new DOMException('quota exceeded', 'QuotaExceededError') }
    expect(saveTabs('1', 'dev', createTabsState(), st)).toEqual({ ok: false, reason: 'quota' })
  })

  it('边界：happy-dom / Firefox 下的等价异常（不同 name、只有 code 22）同样判为 quota', () => {
    for (const err of [
      new DOMException('q', 'NS_ERROR_DOM_QUOTA_REACHED'),
      Object.assign(new Error('full'), { code: 22 }),
    ]) {
      const st = fakeStorage()
      st.setItem = () => { throw err }
      expect(saveTabs('1', 'dev', createTabsState(), st)).toEqual({ ok: false, reason: 'quota' })
    }
  })

  it('边界：写入抛其它异常也按 quota 降级，调用方不需要第二条分支', () => {
    const st = fakeStorage()
    st.setItem = () => { throw new Error('unexpected') }
    expect(saveTabs('1', 'dev', createTabsState(), st)).toEqual({ ok: false, reason: 'quota' })
  })

  it('边界：写入失败时不留半截数据，原来那份仍可读', () => {
    const st = fakeStorage()
    saveTabs('1', 'dev', stateOf([tab('query_1', 'SELECT 1')]), st)
    st.setItem = () => { throw new DOMException('quota exceeded', 'QuotaExceededError') }
    saveTabs('1', 'dev', stateOf([tab('query_1', 'SELECT 2'), tab('query_2', 'SELECT 3')]), st)
    expect(loadTabs('1', 'dev', st)).toEqual(stateOf([tab('query_1', 'SELECT 1')]))
  })
})

describe('sqlTabsStore 边界：(appId, env) 隔离', () => {
  it('同一存储里 (1,dev) / (1,prod) / (2,dev) 三套页签互不串', () => {
    const st = fakeStorage()
    saveTabs('1', 'dev', stateOf([tab('query_1', 'dev 的脚本')]), st)
    saveTabs('1', 'prod', stateOf([tab('query_1', 'prod 的脚本'), tab('query_2', 'prod 第二页')], 1), st)
    saveTabs('2', 'dev', stateOf([tab('query_1', '另一个应用')]), st)
    expect(loadTabs('1', 'dev', st).tabs[0].sql).toBe('dev 的脚本')
    expect(loadTabs('1', 'prod', st)).toEqual(stateOf([tab('query_1', 'prod 的脚本'), tab('query_2', 'prod 第二页')], 1))
    expect(loadTabs('2', 'dev', st).tabs[0].sql).toBe('另一个应用')
  })

  it('边界：换个 env 读不到东西时回默认单页签，不去借 dev 的页签', () => {
    const st = fakeStorage()
    saveTabs('1', 'dev', stateOf([tab('query_1', 'SELECT 1')]), st)
    expect(loadTabs('1', 'prod', st)).toEqual(createTabsState())
  })
})

describe('sqlTabsStore 边界：幂等与无效操作', () => {
  it('边界：removeTab 关一个不存在的 id 原样返回（连点 × 不会把页签掏空）', () => {
    const s = stateOf([tab('query_1', 'a'), tab('query_2', 'b')], 1)
    expect(removeTab(s, 'query_99')).toEqual(s)
  })

  it('边界：关掉当前激活的中间页签，激活位落在后一页而不是前一页', () => {
    const three = stateOf([tab('query_1', 'a'), tab('query_2', 'b'), tab('query_3', 'c')], 1)
    const next = removeTab(three, 'query_2')
    expect(next.tabs.map((t) => t.id)).toEqual(['query_1', 'query_3'])
    expect(next.active).toBe(1) // query_3 接手
  })

  it('边界：关掉当前激活的最后一页，激活位落到它前一位而不是跳回第一页', () => {
    const three = stateOf([tab('query_1', 'a'), tab('query_2', 'b'), tab('query_3', 'c')], 2)
    const next = removeTab(three, 'query_3')
    expect(next.tabs.map((t) => t.id)).toEqual(['query_1', 'query_2'])
    expect(next.active).toBe(1)
  })

  it('边界：renameTab 传空白名保留旧名，不允许页签标题变空', () => {
    const s = stateOf([tab('query_1', 'a')])
    for (const name of ['', '   ', null, undefined]) {
      expect(renameTab(s, 'query_1', name).tabs[0].name).toBe('query_1')
    }
  })

  it('边界：addTab / removeTab 不改传入的 state（页签状态是不可变数据）', () => {
    const s = stateOf([tab('query_1', 'a')])
    const snapshot = JSON.stringify(s)
    addTab(s)
    removeTab(s, 'query_1')
    renameTab(s, 'query_1', '改了')
    expect(JSON.stringify(s)).toBe(snapshot)
  })
})

// 组件把结果块挂在页签对象上（每页签一套结果），所以结构操作只能改页签集合，
// 不能顺手把运行时字段削掉；但落盘仍只走 id/name/sql 白名单。
describe('sqlTabsStore 边界：运行时字段在结构操作里保留', () => {
  const runTab = (id, sql, results, resultIndex = 0) => ({ id, name: id, sql, results, sentCount: results.length, resultIndex })
  const withRuntime = () => ({
    version: TABS_VERSION,
    active: 0,
    tabs: [runTab('query_1', 'SELECT 1', [{ status: 'ok' }, { status: 'error' }], 1), runTab('query_2', 'SELECT 2', [])],
  })

  it('addTab 不动已有页签的结果块，新建页签自身不带结果', () => {
    const next = addTab(withRuntime())
    expect(next.tabs[0].results).toHaveLength(2)
    expect(next.tabs[0].sentCount).toBe(2)
    expect(next.tabs[2]).toEqual({ id: 'query_3', name: 'query_3', sql: '' })
  })

  it('边界：renameTab 改标题不削掉结果块（重命名不是重置）', () => {
    const next = renameTab(withRuntime(), 'query_1', '对账')
    expect(next.tabs[0].name).toBe('对账')
    expect(next.tabs[0].results).toHaveLength(2)
  })

  it('边界：removeTab 只让被关的那个页签消失，其余页签结果不受影响', () => {
    const next = removeTab(withRuntime(), 'query_2')
    expect(next.tabs.map((t) => t.id)).toEqual(['query_1'])
    expect(next.tabs[0].results).toHaveLength(2)
  })

  // Ticket 009：结果页签的停留位跟 results 同一族——结构操作里要跟着页签走，
  // 不然改个名就把人正在看的那一页跳回第 1 条。
  it('边界：resultIndex 在结构操作里跟着自己的页签走，不丢也不串', () => {
    const next = renameTab(withRuntime(), 'query_1', '对账')
    expect(next.tabs[0].resultIndex).toBe(1)
    expect(next.tabs[1].resultIndex).toBe(0)
    expect(addTab(withRuntime()).tabs[2].resultIndex).toBeUndefined() // 新页签没结果，也不预造停留位
  })

  it('边界：保留运行时字段的 state 落盘时仍被削成 id/name/sql', () => {
    const st = fakeStorage()
    expect(saveTabs('1', 'dev', addTab(withRuntime()), st)).toEqual({ ok: true })
    const raw = st.map.get('zc.dbq.tabs.1.dev')
    expect(raw).not.toContain('results')
    expect(raw).not.toContain('sentCount')
    expect(raw).not.toContain('resultIndex') // 结果页签的停留位也不落盘（US-05）
    expect(Object.keys(loadTabs('1', 'dev', st).tabs[0])).toEqual(['id', 'name', 'sql'])
  })
})
