// @vitest-environment happy-dom
// Ticket 002：查询控制台示踪弹——一个框写 `SELECT 1; SELECT 2`，点执行穿
// 「前端拆分 → 批量端点 → 逐条结果块渲染」这一整根竖线。
//
// CodeMirror 6 依赖真实测量与布局，happy-dom 里起不来，故本票把 SqlEditor 桩成
// textarea：控制台的逻辑（切段、确认口径、调用批量端点、渲染）全部在桩外可测。
// 语句级失败与 skipped 归 003、假分号归 004、多页签归 005；006 的选区优先与格式化
// 也只测到“控制台拿到的选区”这一层：CodeMirror 里的真实 keydown 归浏览器实测。
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { h } from 'vue'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { dbSqlBatch: vi.fn() },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn(({ onOk }) => onOk && onOk()) } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => ({ ...mocks.antd, default: { message: mocks.antd.message, Modal: mocks.antd.Modal } }))

import SqlConsole from './SqlConsole.vue'
import { format } from 'sql-formatter'
import { FORMAT_OPTIONS } from '../utils/sqlFormat'
import { MAX_TABS, TABS_VERSION, tabsKey } from '../utils/sqlTabsStore'

// 预期串直接拿 sql-formatter 现算：这些用例锁的是“控制台把哪一段送去、替换了哪个区间”，
// 格式化的具体输出已由 utils/sqlFormat.test.js 逐字钉住，不在这里重复一遗。
const fmt = (s) => format(s, FORMAT_OPTIONS)

const rowsResult = (index, sql, rows, extra = {}) => ({
  index, op: 'select', sql, status: 'ok', duration_ms: 3,
  kind: 'rows', columns: ['n'], rows, truncated: false, ...extra,
})
const affectedResult = (index, sql, affected) => ({
  index, op: 'insert', sql, status: 'ok', duration_ms: 5, kind: 'affected', affected,
})
const errorResult = (index, sql, error) => ({
  index, op: 'select', sql, status: 'error', duration_ms: 2, error,
})
const skippedResult = (index, sql) => ({ index, op: 'select', sql, status: 'skipped' })

// SqlEditor 桩：textarea 双向绑定 + 可控选区读数 + replaceRange 记录（Ticket 006）。
// CodeMirror 在 happy-dom 里起不来，选区与写回这两条通道只能喂给控制台测接线；
// 格式化本身已由 utils/sqlFormat 纯函数逐条锁住，这里不重复验它的输出。
let stubSel = { text: '', from: 0, to: 0 }
let stubReplace = []
const EDITOR_STUB = {
  name: 'SqlEditor',
  props: ['modelValue'],
  emits: ['update:modelValue', 'execute', 'format'],
  render() {
    return h('textarea', { class: 'zc-sql-editor', value: this.modelValue ?? '',
      onInput: (e) => this.$emit('update:modelValue', e.target.value) })
  },
  methods: {
    getSelectionText: () => stubSel.text,
    getSelectionRange: () => ({ from: stubSel.from, to: stubSel.to }),
    // 真实编辑器会就地替换 [from,to)；桩同样从 modelValue 拼出全文往后回写，
    // 这样控制台侧的 v-model 链路（防抖落盘、文本框回显）能跟着一起验。
    // 第四个参数 sel 是“格式化后选区应落在哪”，退化桩不接它，但记录下来了就可断言。
    replaceRange(...args) {
      const [from, to, insert, sel] = args
      stubReplace.push({ from, to, insert, sel: sel ?? null })
      const cur = this.modelValue ?? ''
      this.$emit('update:modelValue', cur.slice(0, from) + insert + cur.slice(to))
    },
    focus: () => {},
  },
}
// 退化桩：只有 v-model 的纯 textarea（DbAdminView.test.js 就是这么桩的），
// 选区与写回方法全都不存在——控制台必须拿 ?.() 吃下。
const TEXTAREA_ONLY_STUB = {
  name: 'SqlEditor',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  render() {
    return h('textarea', { class: 'zc-sql-editor', value: this.modelValue ?? '',
      onInput: (e) => this.$emit('update:modelValue', e.target.value) })
  },
}

const STUBS = {
  SqlEditor: EDITOR_STUB,
  'a-button': { props: ['disabled', 'loading'], render() {
    return h('button', { class: 'zc-sql-btn', disabled: !!this.disabled }, this.$slots.default?.()) } },
  'a-table': { props: ['columns', 'dataSource'], render() {
    return h('table', { class: 'zc-sql-table' }, h('tbody', (this.dataSource || []).map((r, i) =>
      h('tr', { key: i }, Object.values(r).map((v) => h('td', String(v))))))) } },
}

function mountConsole(props = {}, stubs = {}) {
  return mount(SqlConsole, {
    props: { appId: '1', env: 'dev', tables: [{ name: 'widgets' }], ...props },
    global: { stubs: { ...STUBS, ...stubs } },
  })
}

// 页签落盘走 globalThis.localStorage（Ticket 005）。这里换成假 Map：一是 happy-dom 的
// localStorage 会在用例之间串味（上一个用例写的页签被下一个用例读回来），
// 二是能直接断言“落下去的那串 JSON 到底是什么”。
function createStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)) },
    removeItem: (k) => { map.delete(k) },
  }
}

let storage
// 预置一套页签（不预置时 store 返回默认单页签）
function seed(tabs, active = 0, key = tabsKey('1', 'dev')) {
  storage.map.set(key, JSON.stringify({ version: TABS_VERSION, active, tabs }))
}
const saved = (key = tabsKey('1', 'dev')) => (storage.map.has(key) ? JSON.parse(storage.map.get(key)) : null)

async function runWith(text, props = {}) {
  const w = mountConsole(props)
  await flushPromises()
  if (text !== null) await w.find('.zc-sql-editor').setValue(text)
  await w.find('.zc-sql-run').trigger('click')
  await flushPromises()
  return w
}

// Ticket 009：结果区一条语句一个页签，DOM 里同一时刻**只有激活那一块**。
// 要看第 i 条的内容得先点过去——这正是本票要锁的行为，不是测试的额外开销。
async function viewResult(w, i) {
  await w.findAll('.zc-sql-rtab')[i].trigger('click')
  return w.find('.zc-sql-block')
}

// 把当前文本的 [from,to) 框成选中（选区文本由 doc 切出来，与真实编辑器一致）
function selectIn(doc, from, to) {
  stubSel = { from, to, text: doc.slice(from, to) }
  return stubSel
}

async function typeInto(w, text) {
  await w.find('.zc-sql-editor').setValue(text)
  return w
}

beforeEach(() => {
  vi.clearAllMocks()
  stubSel = { text: '', from: 0, to: 0 }
  stubReplace = []
  storage = createStorage()
  vi.stubGlobal('localStorage', storage)
  mocks.antd.Modal.confirm = vi.fn(({ onOk }) => onOk && onOk())
  mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
    rowsResult(0, 'SELECT 1', [{ n: 1 }]), rowsResult(1, 'SELECT 2', [{ n: 2 }]),
  ] } })
})

afterEach(() => { vi.unstubAllGlobals() })

// 每条用例结束就把组件掉回去：控制台里挂着一个 400ms 的防抖写入 timer，
// 上一用例的实例若不卸载，它会在下一用例跑到点，把脚本写进**当前用例**的假存储里，
// 让“本不该落盘”的断言假红。
// （卸载时的 persist 写的是旧用例那个 map，下一个用例拿到的是全新 Map）
enableAutoUnmount(afterEach)

describe('SqlConsole 批量执行（主流程）', () => {
  it('两条 SELECT 拆成数组送批量端点，并渲染两个结果页签（只显示激活那一条）', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledTimes(1)
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledWith('1', 'dev', ['SELECT 1', 'SELECT 2'])
    const rtabs = w.findAll('.zc-sql-rtab')
    expect(rtabs).toHaveLength(2)
    expect(rtabs[0].text()).toContain('第 1/2 条')
    expect(rtabs[1].text()).toContain('第 2/2 条')
    expect(rtabs[0].classes()).toContain('zc-sql-rtab--active')
    // 纵向堆叠已被水平导航替掉：同一时刻只有一块在 DOM 里
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.find('.zc-sql-block').text()).toContain('第 1/2 条')
    expect(w.findAll('.zc-sql-table')).toHaveLength(1)
    expect(w.find('.zc-sql-table').text()).toContain('1')
  })

  it('点第二个结果页签：内容切成第 2 条，第 1 条的表格不在 DOM 里', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    const second = await viewResult(w, 1)
    expect(second.text()).toContain('第 2/2 条')
    expect(second.text()).toContain('SELECT 2')
    expect(second.findAll('.zc-sql-table td').map((t) => t.text())).toEqual(['2'])
    expect(w.findAll('.zc-sql-rtab')[1].classes()).toContain('zc-sql-rtab--active')
    expect(w.findAll('.zc-sql-rtab')[0].classes()).not.toContain('zc-sql-rtab--active')
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
  })

  it('结果块头部带 op 与耗时，让每条各自可辨认', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.find('.zc-sql-block').text()).toContain('select')
    expect(w.find('.zc-sql-block').text()).toContain('3ms')
    // 页签标题上也带 op，不用点进去就能辨认这一条是什么
    expect(w.findAll('.zc-sql-rtab')[0].text()).toContain('select')
  })

  it('混合段按序渲染：写显示影响行数、读显示表格', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      affectedResult(0, "INSERT INTO widgets VALUES (1,'a')", 1),
      rowsResult(1, 'SELECT * FROM widgets', [{ id: 1 }, { id: 2 }]),
    ] } })
    const w = await runWith("INSERT INTO widgets VALUES (1,'a'); SELECT * FROM widgets")
    expect(w.find('.zc-sql-block').text()).toContain('影响 1 行')
    expect(w.find('.zc-sql-block').find('.zc-sql-table').exists()).toBe(false)
    const second = await viewResult(w, 1)
    expect(second.find('.zc-sql-table').exists()).toBe(true)
    expect(second.findAll('.zc-sql-table tr')).toHaveLength(2)
  })

  it('含非只读语句时整批只确认一次，确认后执行', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      affectedResult(0, 'UPDATE widgets SET name=1', 1),
      affectedResult(1, 'DELETE FROM widgets', 1),
    ] } })
    const w = await runWith('UPDATE widgets SET name=1; DELETE FROM widgets')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledTimes(1)
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
  })

  it('纯读段不弹确认', async () => {
    await runWith('SELECT 1; SELECT 2')
    expect(mocks.antd.Modal.confirm).not.toHaveBeenCalled()
  })

  it('截断的 SELECT 结果页签展示截断提示，未截断的那页不展示', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      rowsResult(0, 'SELECT 1', [{ n: 1 }], { truncated: true }), rowsResult(1, 'SELECT 2', [{ n: 2 }]),
    ] } })
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.find('.zc-sql-block').find('.zc-sql-truncated').exists()).toBe(true)
    expect((await viewResult(w, 1)).find('.zc-sql-truncated').exists()).toBe(false)
    // 切回来时提示还在：截断是每条自己的属性，不随页签切换丢失
    expect((await viewResult(w, 0)).find('.zc-sql-truncated').exists()).toBe(true)
  })
})

describe('SqlConsole 边界', () => {
  it('取消二次确认则一条都不执行', async () => {
    mocks.antd.Modal.confirm = vi.fn() // 弹了但用户取消（不调 onOk）
    const w = await runWith('UPDATE widgets SET name=1')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    expect(mocks.api.dbSqlBatch).not.toHaveBeenCalled()
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
  })

  it('边界：空输入点执行不打后端并提示', async () => {
    const w = await runWith('')
    expect(mocks.api.dbSqlBatch).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalled()
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
  })

  it('边界：只有分号与空白的输入不打后端', async () => {
    const w = await runWith('  ; ;\n;  ')
    expect(mocks.api.dbSqlBatch).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalled()
  })

  it('边界：尾部分号不产生第三条空语句', async () => {
    await runWith('SELECT 1; SELECT 2;')
    expect(mocks.api.dbSqlBatch.mock.calls[0][2]).toEqual(['SELECT 1', 'SELECT 2'])
  })

  it('边界：无分号的单条也照常走批量端点', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [rowsResult(0, 'SELECT 1', [{ n: 1 }])] } })
    const w = await runWith('SELECT 1')
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledWith('1', 'dev', ['SELECT 1'])
    // 单条结果不渲染页签栏（009 裁定 4）：一个页签的页签栏是自造的复杂度
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.find('.zc-sql-block').text()).toContain('第 1/1 条')
  })

  it('边界：执行打到当前 env（prod 亦是同一端点）', async () => {
    await runWith('SELECT 1', { env: 'prod' })
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledWith('1', 'prod', ['SELECT 1'])
  })

  it('边界：切换 env 清空上一次结果，不拿 dev 的结果糊 prod', async () => {
    const w = await runWith('SELECT 1')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    await w.setProps({ env: 'prod' })
    await flushPromises()
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
  })

  it('边界：请求失败错误上屏且不残留旧结果', async () => {
    mocks.api.dbSqlBatch.mockRejectedValue({ response: { data: { detail: '目标库不可达' } } })
    const w = await runWith('SELECT 1')
    expect(mocks.antd.message.error).toHaveBeenCalledWith('目标库不可达')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
  })

  it('边界：执行期间按钮禁用，返回后恢复（防连点双发）', async () => {
    let done
    mocks.api.dbSqlBatch.mockImplementation(() => new Promise((r) => { done = r }))
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 1')
    await w.find('.zc-sql-run').trigger('click')
    expect(w.find('.zc-sql-run').attributes('disabled')).toBeDefined()
    done({ data: { results: [rowsResult(0, 'SELECT 1', [{ n: 1 }])] } })
    await flushPromises()
    expect(w.find('.zc-sql-run').attributes('disabled')).toBeUndefined()
  })
})

describe('SqlConsole 遇错即停与失败可见（Ticket 003）', () => {
  const threeStates = () => mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
    affectedResult(0, 'INSERT INTO widgets VALUES (21,\'keep\')', 1),
    errorResult(1, 'SELECT * FROM no_such_table', 'relation "no_such_table" does not exist'),
    skippedResult(2, 'SELECT 3 AS n'),
  ] } })

  it('第 2 条失败：页签带 error/skipped 状态并默认停在失败页签，块带 PG 原文', async () => {
    threeStates()
    const w = await runWith("INSERT INTO widgets VALUES (21,'keep'); SELECT * FROM no_such_table; SELECT 3 AS n")
    const rtabs = w.findAll('.zc-sql-rtab')
    expect(rtabs).toHaveLength(3)
    expect(rtabs[0].classes()).not.toContain('zc-sql-rtab--error')
    expect(rtabs[1].classes()).toContain('zc-sql-rtab--error')
    expect(rtabs[1].text()).toContain('失败')
    expect(rtabs[2].classes()).toContain('zc-sql-rtab--skipped')
    expect(rtabs[2].text()).toContain('未执行')
    // 009 裁定 3：执行回来自动停在第一个失败页签，不用点就知道哪条炸了
    expect(rtabs[1].classes()).toContain('zc-sql-rtab--active')
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.find('.zc-sql-block').classes()).toContain('zc-sql-block--error')
    expect(w.find('.zc-sql-block').text()).toContain('relation "no_such_table" does not exist')
    const third = await viewResult(w, 2)
    expect(third.classes()).toContain('zc-sql-block--skipped')
    expect(third.text()).toContain('未执行')
  })

  it('汇总条：共 N 条、成功 X、失败于第 i 条、剩余 M 条未执行', async () => {
    threeStates()
    const w = await runWith("INSERT INTO widgets VALUES (21,'keep'); SELECT * FROM no_such_table; SELECT 3 AS n")
    expect(w.find('.zc-sql-summary').text())
      .toBe('共 3 条，成功 1，失败于第 2 条，剩余 1 条未执行')
  })

  it('边界：全部成功时汇总不出现失败与未执行子句', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.find('.zc-sql-summary').text()).toBe('共 2 条，成功 2')
  })

  it('边界：第 1 条即错 → 成功 0、剩余全部未执行', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      errorResult(0, 'SELECT * FROM no_such_table', 'relation "no_such_table" does not exist'),
      skippedResult(1, 'SELECT 1 AS a'), skippedResult(2, 'SELECT 2 AS b'),
    ] } })
    const w = await runWith('SELECT * FROM no_such_table; SELECT 1 AS a; SELECT 2 AS b')
    expect(w.find('.zc-sql-summary').text())
      .toBe('共 3 条，成功 0，失败于第 1 条，剩余 2 条未执行')
    // 状态徽章挂在页签上，与"只渲染激活块"无关：三条各自的状态一眼可见
    expect(w.findAll('.zc-sql-rtab--skipped')).toHaveLength(2)
    expect(w.findAll('.zc-sql-rtab--error')).toHaveLength(1)
    // 失败在第 1 条 → 落点就是第 1 个页签，渲染出来的那块带 error 修饰
    expect(w.findAll('.zc-sql-rtab')[0].classes()).toContain('zc-sql-rtab--active')
    expect(w.find('.zc-sql-block').classes()).toContain('zc-sql-block--error')
  })

  it('边界：最后一条失败（后面无未执行）→ 汇总不带「剩余」子句', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      rowsResult(0, 'SELECT 1', [{ n: 1 }]),
      errorResult(1, 'SELECT * FROM nope', 'relation "nope" does not exist'),
    ] } })
    const w = await runWith('SELECT 1; SELECT * FROM nope')
    expect(w.find('.zc-sql-summary').text()).toBe('共 2 条，成功 1，失败于第 2 条')
    // 失败在最后一条：落点仍跟着失败走，不停在第 1 条上
    expect(w.findAll('.zc-sql-rtab')[1].classes()).toContain('zc-sql-rtab--active')
    expect(w.find('.zc-sql-block').classes()).toContain('zc-sql-block--error')
  })

  it('边界：未执行块不编造结果体（不画表格、不写影响行数）', async () => {
    threeStates()
    const w = await runWith("INSERT INTO widgets VALUES (21,'keep'); SELECT * FROM no_such_table; SELECT 3 AS n")
    const skipped = await viewResult(w, 2)
    expect(skipped.find('.zc-sql-table').exists()).toBe(false)
    expect(skipped.text()).not.toContain('影响')
    expect(skipped.text()).not.toContain('成功')
  })

  it('边界：失败块不画表格，只把原文经 <pre> 保留换行', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      errorResult(0, 'SELECT * FROM a\nJOIN b', 'relation "a" does not exist\nLINE 2: JOIN b'),
    ] } })
    const w = await runWith('SELECT * FROM a\nJOIN b')
    const err = w.find('.zc-sql-error-text')
    expect(err.exists()).toBe(true)
    expect(err.element.tagName.toLowerCase()).toBe('pre')
    expect(err.text()).toContain('LINE 2: JOIN b')
    expect(w.find('.zc-sql-table').exists()).toBe(false)
  })

  it('边界：数组里带纯注释段时 index 仍按原数组，块头分母是发出去的条数', async () => {
    // 004 之后前端拆分不再产出注释段，但后端那道 has_executable_content 兜底仍在
    // （旁路进来的数组，如选区执行/外部填充，可能带注释段），结果集 index 会稀疏。
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [rowsResult(2, 'SELECT 3 AS n', [{ n: 3 }])] } })
    const w = mountConsole()
    await flushPromises()
    await w.vm.submit(['-- 一', '-- 二', 'SELECT 3 AS n'])
    await flushPromises()
    // 只有 1 条结果 → 不出现页签栏，但块头分母仍是发出去的 3 条
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.find('.zc-sql-block').text()).toContain('第 3/3 条')
    expect(w.find('.zc-sql-summary').text()).toBe('共 3 条，成功 1')
  })

  it('边界：重新执行后旧的 error/skipped 块与汇总不残留', async () => {
    threeStates()
    const w = await runWith("INSERT INTO widgets VALUES (21,'keep'); SELECT * FROM no_such_table; SELECT 3 AS n")
    expect(w.findAll('.zc-sql-block--error')).toHaveLength(1)
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [rowsResult(0, 'SELECT 1', [{ n: 1 }])] } })
    await w.find('.zc-sql-editor').setValue('SELECT 1')
    await w.find('.zc-sql-run').trigger('click')
    await flushPromises()
    // 3 条 → 1 条：原先停在第 2 个页签，越界的激活位要钳回 0，不能渲染成空白面板
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.find('.zc-sql-block').text()).toContain('第 1/1 条')
    expect(w.findAll('.zc-sql-block--error')).toHaveLength(0)
    expect(w.find('.zc-sql-summary').text()).toBe('共 1 条，成功 1')
  })

  it('边界：503（整体拒绝）走 message.error，不画任何结果块也不画汇总', async () => {
    mocks.api.dbSqlBatch.mockRejectedValue({
      response: { data: { detail: '数据库连接失败，请检查 zerocode-pg16：' } } })
    const w = await runWith('SELECT 1; SELECT 2')
    expect(mocks.antd.message.error).toHaveBeenCalledWith('数据库连接失败，请检查 zerocode-pg16：')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
    expect(w.find('.zc-sql-summary').exists()).toBe(false)
  })
})

describe('SqlConsole 假分号拆分（Ticket 004）', () => {
  it("引号里的分号不切：SELECT 'a;b' AS x; SELECT 2 → 两个结果页签", async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      rowsResult(0, "SELECT 'a;b' AS x", [{ x: 'a;b' }]), rowsResult(1, 'SELECT 2', [{ n: 2 }]),
    ] } })
    const w = await runWith("SELECT 'a;b' AS x; SELECT 2")
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledWith('1', 'dev', ["SELECT 'a;b' AS x", 'SELECT 2'])
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    const block = w.find('.zc-sql-block')
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(block.text()).toContain("SELECT 'a;b' AS x")
    expect(block.findAll('.zc-sql-table td')[0].text()).toBe('a;b')
  })

  it('dollar-quote 函数体内的分号不切：CREATE FUNCTION … $$ … $$ ; SELECT 1 → 恰好两条', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      affectedResult(0, 'CREATE FUNCTION', 0), rowsResult(1, 'SELECT 1', [{ n: 1 }]),
    ] } })
    await runWith('CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql; SELECT 1')
    expect(mocks.api.dbSqlBatch.mock.calls[0][2]).toEqual([
      'CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql', 'SELECT 1',
    ])
  })

  it('边界：整段只有注释 → 前端就不发请求，提示没有可执行的语句', async () => {
    const w = await runWith('-- 只是注释\n/* 也没别的 */')
    expect(mocks.api.dbSqlBatch).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalledWith('没有可执行的语句')
  })

  it('边界：未闭合引号不瞎猜拆分点，原文整段送后端由 PG 报原文错误', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      errorResult(0, "SELECT 'a;b", 'syntax error at or near "\'a;b"'),
    ] } })
    const w = await runWith("SELECT 'a;b")
    expect(mocks.api.dbSqlBatch.mock.calls[0][2]).toEqual(["SELECT 'a;b"])
    expect(w.find('.zc-sql-block--error').text()).toContain('syntax error')
  })
})

describe('SqlConsole 结果面板与空态（样式轮）', () => {
  it('边界：未执行时空态占位，不画汇总也不假装「共 0 条」', async () => {
    const w = mountConsole()
    await flushPromises()
    expect(w.find('.zc-sql-empty').text()).toBe('尚未执行')
    expect(w.find('.zc-sql-results-head').text()).toContain('执行结果')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.find('.zc-sql-rtabs').exists()).toBe(false)
    expect(w.find('.zc-sql-summary').exists()).toBe(false)
  })

  it('边界：拿到结果后空态消失，结果区只留激活那一块', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.find('.zc-sql-empty').exists()).toBe(false)
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
  })

  it('边界：整体请求失败后回到空态，不残留半截结果', async () => {
    mocks.api.dbSqlBatch.mockRejectedValue({ response: { data: { detail: '目标库不可达' } } })
    const w = await runWith('SELECT 1')
    expect(w.find('.zc-sql-empty').text()).toBe('尚未执行')
    expect(w.find('.zc-sql-summary').exists()).toBe(false)
  })

  it('块头状态：失败页签与失败块标「失败」、未执行标「未执行」、成功条不挂重复状态', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      affectedResult(0, "INSERT INTO widgets VALUES (1,'a')", 1),
      errorResult(1, 'SELECT * FROM nope', 'relation "nope" does not exist'),
      skippedResult(2, 'SELECT 3 AS n'),
    ] } })
    const w = await runWith("INSERT INTO widgets VALUES (1,'a'); SELECT * FROM nope; SELECT 3 AS n")
    // 页签栏上三条的状态直接可见，不用逐页点开数
    const rtabs = w.findAll('.zc-sql-rtab')
    expect(rtabs[0].find('.zc-sql-rtab-state').exists()).toBe(false)
    expect(rtabs[1].find('.zc-sql-rtab-state').text()).toBe('失败')
    expect(rtabs[2].find('.zc-sql-rtab-state').text()).toBe('未执行')
    // 块内 chip 只属于当前渲染的这一块：默认停在失败页，就只有一颗「失败」
    const chips = w.findAll('.zc-sql-block-state')
    expect(chips).toHaveLength(1)
    expect(chips[0].classes()).toContain('zc-sql-block-state--error')
    expect(chips[0].text()).toBe('失败')
    // 切到成功那条不拿别的页签的状态糊人
    expect((await viewResult(w, 0)).find('.zc-sql-block-state').exists()).toBe(false)
    expect((await viewResult(w, 2)).find('.zc-sql-block-state').text()).toBe('未执行')
  })

  it('边界：耗时 null 不渲染成「nullms」', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      { ...rowsResult(0, 'SELECT 1', [{ n: 1 }]), duration_ms: null },
    ] } })
    const w = await runWith('SELECT 1')
    expect(w.find('.zc-sql-block').find('.zc-sql-block-cost').exists()).toBe(false)
  })
})

describe('SqlConsole 多页签与本地保存（Ticket 005）', () => {
  it('无存档时只有一个默认页签 query_1，结果头跟着页签名走', async () => {
    const w = mountConsole()
    await flushPromises()
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
    expect(w.find('.zc-sql-tab-name').text()).toBe('query_1')
    expect(w.find('.zc-sql-results-head').text()).toContain('query_1')
  })

  it('挂载时从 localStorage 恢复：页签名称、顺序、激活位与当前页文本原样回来', async () => {
    seed([
      { id: 'query_1', name: 'query_1', sql: 'SELECT 1' },
      { id: 'query_2', name: 'query_2', sql: 'SELECT 2' },
      { id: 'report', name: '月度对账', sql: '  select 4  ' },
    ], 2)
    const w = mountConsole()
    await flushPromises()
    expect(w.findAll('.zc-sql-tab-name').map((n) => n.text())).toEqual(['query_1', 'query_2', '月度对账'])
    expect(w.findAll('.zc-sql-tab')[2].classes()).toContain('zc-sql-tab--active')
    expect(w.find('.zc-sql-editor').element.value).toBe('  select 4  ')
  })

  it('点「+」新建：新页签追加末尾并接管激活，文本框换成空白页', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 1')
    await w.find('.zc-sql-tab-add').trigger('click')
    expect(w.findAll('.zc-sql-tab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-tab')[1].classes()).toContain('zc-sql-tab--active')
    expect(w.find('.zc-sql-editor').element.value).toBe('')
    // 结构性动作不等防抖：旧页的打字进度当场落盘
    expect(saved()).toEqual({ version: TABS_VERSION, active: 1,
      tabs: [{ id: 'query_1', name: 'query_1', sql: 'SELECT 1' }, { id: 'query_2', name: 'query_2', sql: '' }] })
  })

  it('切换页签：文本与结果各归各页，切回来还在', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    await w.find('.zc-sql-tab-add').trigger('click')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
    expect(w.find('.zc-sql-editor').element.value).toBe('')
    await w.findAll('.zc-sql-tab')[0].trigger('click')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.find('.zc-sql-editor').element.value).toBe('SELECT 1; SELECT 2')
    expect(w.find('.zc-sql-summary').exists()).toBe(true)
  })

  it('边界：打字未到防抖点就卸载 → unmount 兜底 flush，重挂不丢脚本', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 半小时前写的脚本')
    expect(saved()).toBeNull() // 防抖窗口内确实还没写
    w.unmount()
    expect(saved().tabs[0].sql).toBe('SELECT 半小时前写的脚本')
    const again = mountConsole()
    await flushPromises()
    expect(again.find('.zc-sql-editor').element.value).toBe('SELECT 半小时前写的脚本')
  })

  it('边界：结果只存内存，落盘与重挂后结果区为空（US-05）', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    await viewResult(w, 1) // 停在第 2 个结果页签：这个停留位也不能落盘
    w.unmount()
    expect(saved().tabs[0]).not.toHaveProperty('results')
    expect(saved().tabs[0]).not.toHaveProperty('sentCount')
    expect(saved().tabs[0]).not.toHaveProperty('resultIndex')
    const again = mountConsole()
    await flushPromises()
    expect(again.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(again.findAll('.zc-sql-block')).toHaveLength(0)
    expect(again.find('.zc-sql-empty').text()).toBe('尚未执行')
    expect(again.find('.zc-sql-summary').exists()).toBe(false)
  })

  it('双击原地重命名：只改标题不改 id，落盘的是新标题', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 1')
    await w.findAll('.zc-sql-tab')[0].trigger('dblclick')
    const input = w.find('.zc-sql-tab-input')
    expect(input.exists()).toBe(true)
    expect(input.attributes('aria-label')).toBe('重命名页签')
    await input.setValue('月度对账')
    await input.trigger('keyup', { key: 'Enter' })
    expect(w.find('.zc-sql-tab-input').exists()).toBe(false)
    expect(w.find('.zc-sql-tab-name').text()).toBe('月度对账')
    expect(saved().tabs[0]).toEqual({ id: 'query_1', name: '月度对账', sql: 'SELECT 1' })
  })

  it('关闭非空页签弹一次确认，确认后该页签连同它的结果一起消失', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    await w.find('.zc-sql-tab-add').trigger('click')
    await w.findAll('.zc-sql-tab')[0].find('.zc-sql-tab-close').trigger('click')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
    expect(w.find('.zc-sql-tab-name').text()).toBe('query_2')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
    expect(saved().tabs.map((t) => t.id)).toEqual(['query_2'])
  })

  it('边界：结果回来时人已切走，in-flight 结果仍落回发起那一页', async () => {
    let settle
    mocks.api.dbSqlBatch.mockImplementation(() => new Promise((r) => { settle = r }))
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 1; SELECT 2')
    await w.find('.zc-sql-run').trigger('click')
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledTimes(1)
    await w.find('.zc-sql-tab-add').trigger('click')
    settle({ data: { results: [rowsResult(0, 'SELECT 1', [{ n: 1 }]), rowsResult(1, 'SELECT 2', [{ n: 2 }])] } })
    await flushPromises()
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0) // 当前页（新建的空页）没拿到别人的结果
    await w.findAll('.zc-sql-tab')[0].trigger('click')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
  })
})

describe('SqlConsole 边界：页签上限与关闭兜底', () => {
  const twelve = () => Array.from({ length: MAX_TABS }, (_, i) => ({
    id: `query_${i + 1}`, name: `query_${i + 1}`, sql: `SELECT ${i + 1}` }))

  it('边界：第 13 个被拒并提示上限，已有页签一个不少', async () => {
    seed(twelve())
    const w = mountConsole()
    await flushPromises()
    expect(w.findAll('.zc-sql-tab')).toHaveLength(MAX_TABS)
    await w.find('.zc-sql-tab-add').trigger('click')
    expect(w.findAll('.zc-sql-tab')).toHaveLength(MAX_TABS)
    expect(mocks.antd.message.warning).toHaveBeenCalledWith(`最多 ${MAX_TABS} 个页签，请先关闭一些`)
    expect(mocks.antd.Modal.confirm).not.toHaveBeenCalled()
  })

  it('边界：历史存档超过上限也不丢脚本，只在新增时拦', async () => {
    seed(Array.from({ length: MAX_TABS + 2 }, (_, i) => ({
      id: `query_${i + 1}`, name: `query_${i + 1}`, sql: `SELECT ${i + 1}` })))
    const w = mountConsole()
    await flushPromises()
    expect(w.findAll('.zc-sql-tab')).toHaveLength(MAX_TABS + 2)
    expect(w.findAll('.zc-sql-tab-name')[MAX_TABS + 1].text()).toBe(`query_${MAX_TABS + 2}`)
  })

  it('边界：关掉唯一那个非空页签后自动补空白页，不出现 0 页签死状态', async () => {
    const w = await runWith('SELECT 1')
    await w.find('.zc-sql-tab-close').trigger('click')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
    expect(w.find('.zc-sql-tab-name').text()).toBe('query_1')
    expect(w.find('.zc-sql-editor').element.value).toBe('')
    expect(w.find('.zc-sql-empty').text()).toBe('尚未执行')
    expect(saved().tabs).toEqual([{ id: 'query_1', name: 'query_1', sql: '' }])
  })

  it('边界：空页签点 × 不弹确认直接关（为“关个空页”再问一次是自找麻烦）', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-tab-add').trigger('click')
    mocks.antd.Modal.confirm.mockClear()
    await w.findAll('.zc-sql-tab')[1].find('.zc-sql-tab-close').trigger('click')
    expect(mocks.antd.Modal.confirm).not.toHaveBeenCalled()
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
  })

  it('边界：确认框被取消时不关页签、不丢脚本', async () => {
    mocks.antd.Modal.confirm = vi.fn() // 用户点了取消： onOk 不被调
    const w = await runWith('SELECT 1')
    await w.find('.zc-sql-tab-close').trigger('click')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
    expect(w.find('.zc-sql-editor').element.value).toBe('SELECT 1')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
  })

  it('边界：等待期间关掉发起页，补上来的同名空白页不继承别人的结果', async () => {
    let settle
    mocks.api.dbSqlBatch.mockImplementation(() => new Promise((r) => { settle = r }))
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 1; SELECT 2')
    await w.find('.zc-sql-run').trigger('click')
    await w.find('.zc-sql-tab-close').trigger('click') // 唯一非空页，confirm 桩直接 onOk
    expect(w.find('.zc-sql-tab-name').text()).toBe('query_1') // 补上来的新页与发起页同名
    settle({ data: { results: [rowsResult(0, 'SELECT 1', [{ n: 1 }]), rowsResult(1, 'SELECT 2', [{ n: 2 }])] } })
    await flushPromises()
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
    expect(w.find('.zc-sql-empty').text()).toBe('尚未执行')
  })

  it('边界：重命名交空白名 → 保留原标题，不允许页签变成无名', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.findAll('.zc-sql-tab')[0].trigger('dblclick')
    await w.find('.zc-sql-tab-input').setValue('   ')
    await w.find('.zc-sql-tab-input').trigger('blur')
    expect(w.find('.zc-sql-tab-name').text()).toBe('query_1')
    expect(w.find('.zc-sql-tab-input').exists()).toBe(false)
  })

  it('边界：Esc 取消重命名，既不落盘也不改标题', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.findAll('.zc-sql-tab')[0].trigger('dblclick')
    await w.find('.zc-sql-tab-input').setValue('改了但没提交')
    await w.find('.zc-sql-tab-input').trigger('keyup', { key: 'Escape' })
    expect(w.find('.zc-sql-tab-name').text()).toBe('query_1')
    expect(saved()).toBeNull()
  })
})

describe('SqlConsole 边界：切环境即切整套页签', () => {
  it('边界：切到 prod 前 dev 那套先落盘，prod 读到的是自己的默认单页签', async () => {
    seed([{ id: 'query_1', name: 'query_1', sql: 'SELECT dev' }, { id: 'query_2', name: 'query_2', sql: 'SELECT dev2' }], 1)
    const w = mountConsole()
    await flushPromises()
    await w.setProps({ env: 'prod' })
    await flushPromises()
    expect(saved(tabsKey('1', 'dev'))).toEqual({ version: TABS_VERSION, active: 1,
      tabs: [{ id: 'query_1', name: 'query_1', sql: 'SELECT dev' }, { id: 'query_2', name: 'query_2', sql: 'SELECT dev2' }] })
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
    expect(w.find('.zc-sql-editor').element.value).toBe('')
  })

  it('边界：切走再切回，dev 的页签与打字进度原样回来', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT dev 的脚本')
    await w.find('.zc-sql-tab-add').trigger('click') // 切页本身就把旧页落盘
    await w.setProps({ env: 'prod' })
    await flushPromises()
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
    await w.setProps({ env: 'dev' })
    await flushPromises()
    expect(w.findAll('.zc-sql-tab-name').map((n) => n.text())).toEqual(['query_1', 'query_2'])
    expect(w.find('.zc-sql-editor').element.value).toBe('') // 回来时仍站在上次激活的那一页（query_2）
    await w.findAll('.zc-sql-tab')[0].trigger('click')
    expect(w.find('.zc-sql-editor').element.value).toBe('SELECT dev 的脚本')
  })

  it('边界：打字打到一半切 env → 进度还给旧键，新键不被写入半个字', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT dev 的半成品')
    await w.setProps({ env: 'prod' })
    await flushPromises()
    expect(saved(tabsKey('1', 'dev')).tabs[0].sql).toBe('SELECT dev 的半成品')
    expect(saved(tabsKey('1', 'prod'))).toBeNull()
  })

  it('边界：等待期间切到 prod，dev 的 in-flight 结果不糊到 prod 的同名页签上', async () => {
    let settle
    mocks.api.dbSqlBatch.mockImplementation(() => new Promise((r) => { settle = r }))
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 1; SELECT 2')
    await w.find('.zc-sql-run').trigger('click')
    await w.setProps({ env: 'prod' })
    await flushPromises()
    settle({ data: { results: [rowsResult(0, 'SELECT 1', [{ n: 1 }]), rowsResult(1, 'SELECT 2', [{ n: 2 }])] } })
    await flushPromises()
    // prod 自己那套只有一个默认 query_1，它不该凭空长出 dev 的两个结果页签
    expect(w.findAll('.zc-sql-tab-name').map((n) => n.text())).toEqual(['query_1'])
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
    expect(w.find('.zc-sql-empty').text()).toBe('尚未执行')
    expect(w.vm.busy).toBe(false)
  })

  it('边界：appId 变化同样换一套，不借上一个应用的页签', async () => {
    seed([{ id: 'query_1', name: 'query_1', sql: '另一个应用的脚本' }], 0, tabsKey('2', 'dev'))
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 1')
    await w.setProps({ appId: '2' })
    await flushPromises()
    expect(w.find('.zc-sql-editor').element.value).toBe('另一个应用的脚本')
    expect(saved(tabsKey('1', 'dev')).tabs[0].sql).toBe('SELECT 1')
  })
})

describe('SqlConsole 边界：本地存储降级', () => {
  const quotaFull = () => {
    storage.setItem = () => { throw new DOMException('quota exceeded', 'QuotaExceededError') }
  }

  it('边界：写入抛 QuotaExceededError → 出黄条且已写的脚本不被清空', async () => {
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 写了半小时的脚本')
    quotaFull()
    await w.find('.zc-sql-tab-add').trigger('click')
    await flushPromises()
    const warn = w.find('.zc-sql-storage-warn')
    expect(warn.exists()).toBe(true)
    expect(warn.attributes('role')).toBe('alert')
    expect(warn.text()).toContain('不再自动保存')
    expect(w.vm.saveBlocked).toBe(true)
    // 黄条只说“存不上”，不能让用户的文本让路：切回那一页，脚本还在
    await w.findAll('.zc-sql-tab')[0].trigger('click')
    expect(w.find('.zc-sql-editor').element.value).toBe('SELECT 写了半小时的脚本')
  })

  it('边界：黄条出现后本次会话不再尝试写入（不跟已满的配额死磕）', async () => {
    const setItem = vi.fn(() => { throw new DOMException('quota exceeded', 'QuotaExceededError') })
    storage.setItem = setItem
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-tab-add').trigger('click')
    expect(w.vm.saveBlocked).toBe(true)
    setItem.mockClear()
    await w.find('.zc-sql-tab-add').trigger('click')
    await w.find('.zc-sql-editor').setValue('又写了一长段')
    w.unmount() // 卸载里的兜底 flush 也不该再试
    expect(setItem).not.toHaveBeenCalled()
  })

  it('边界：localStorage 整个不可用（隐私模式）→ 挂载不抛，写的时候同样出黄条', async () => {
    vi.stubGlobal('localStorage', undefined)
    const w = mountConsole()
    await flushPromises()
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
    expect(w.find('.zc-sql-storage-warn').exists()).toBe(false)
    await w.find('.zc-sql-tab-add').trigger('click')
    expect(w.find('.zc-sql-storage-warn').exists()).toBe(true)
    expect(w.findAll('.zc-sql-tab')).toHaveLength(2)
  })

  it('边界：黄条期间页签照常可用（存储问题不让编辑器让路）', async () => {
    const w = mountConsole()
    await flushPromises()
    quotaFull()
    await w.find('.zc-sql-tab-add').trigger('click')
    expect(w.vm.saveBlocked).toBe(true)
    await w.find('.zc-sql-editor').setValue('SELECT 1; SELECT 2')
    await w.find('.zc-sql-run').trigger('click')
    await flushPromises()
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.findAll('.zc-sql-tab')).toHaveLength(2)
    expect(w.vm.saveBlocked).toBe(true) // 执行成功也不重置黄条
  })

  it('边界：读盘抛异常不弹黄条——黄条只代表“存不下去”而不是“读不着”', async () => {
    storage.getItem = () => { throw new Error('blocked by policy') }
    const w = mountConsole()
    await flushPromises()
    expect(w.findAll('.zc-sql-tab')).toHaveLength(1)
    expect(w.find('.zc-sql-tab-name').text()).toBe('query_1')
    expect(w.find('.zc-sql-storage-warn').exists()).toBe(false)
    expect(w.vm.saveBlocked).toBe(false)
  })
})

describe('SqlConsole 结果页签一条一页（Ticket 009）', () => {
  it('全成功时默认停在第 1 个结果页签，汇总条不随页签切换改变', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.findAll('.zc-sql-rtab')[0].classes()).toContain('zc-sql-rtab--active')
    expect(w.findAll('.zc-sql-rtab')[0].attributes('aria-selected')).toBe('true')
    expect(w.findAll('.zc-sql-rtab')[1].attributes('aria-selected')).toBe('false')
    expect(w.find('.zc-sql-rtabs').attributes('role')).toBe('tablist')
    const summary = w.find('.zc-sql-summary').text()
    await viewResult(w, 1)
    // 页签只是同一批结果的水平导航：汇总始终是整批的汇总
    expect(w.find('.zc-sql-summary').text()).toBe(summary)
    expect(w.find('.zc-sql-summary').text()).toBe('共 2 条，成功 2')
  })

  it('边界：单条结果不渲染页签栏（一个页签的页签栏是自造的复杂度）', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [rowsResult(0, 'SELECT 1', [{ n: 1 }])] } })
    const w = await runWith('SELECT 1')
    expect(w.find('.zc-sql-rtabs').exists()).toBe(false)
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
  })

  it('边界：执行中不渲染空页签栏，只给空态占位', async () => {
    let settle
    mocks.api.dbSqlBatch.mockImplementation(() => new Promise((r) => { settle = r }))
    const w = mountConsole()
    await flushPromises()
    await w.find('.zc-sql-editor').setValue('SELECT 1; SELECT 2')
    await w.find('.zc-sql-run').trigger('click')
    expect(w.find('.zc-sql-rtabs').exists()).toBe(false)
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    expect(w.find('.zc-sql-empty').text()).toBe('执行中…')
    settle({ data: { results: [rowsResult(0, 'SELECT 1', [{ n: 1 }]), rowsResult(1, 'SELECT 2', [{ n: 2 }])] } })
    await flushPromises()
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
  })

  it('边界：后端丢弃注释段后页签标题仍用原数组下标（第 2/3 条、第 3/3 条）', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      rowsResult(1, 'SELECT 2', [{ n: 2 }]), rowsResult(2, 'SELECT 3', [{ n: 3 }]),
    ] } })
    const w = mountConsole()
    await flushPromises()
    await w.vm.submit(['-- 只是注释', 'SELECT 2', 'SELECT 3'])
    await flushPromises()
    const rtabs = w.findAll('.zc-sql-rtab')
    expect(rtabs[0].text()).toContain('第 2/3 条')
    expect(rtabs[1].text()).toContain('第 3/3 条')
    // 落点钳在页签数组位 0（不是结果里的 index 1）：默认停在能看的第一页
    expect(rtabs[0].classes()).toContain('zc-sql-rtab--active')
    expect(w.find('.zc-sql-block').text()).toContain('第 2/3 条')
  })

  it('边界：结果页签的停留位挂在脚本页签对象上，切来切去不串台', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    await viewResult(w, 1)
    await w.find('.zc-sql-tab-add').trigger('click') // 新页签自己停在“无结果”
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
    await w.findAll('.zc-sql-tab')[0].trigger('click')
    // 旧页签的停留位还在：不需要重新点一遍
    expect(w.findAll('.zc-sql-rtab')[1].classes()).toContain('zc-sql-rtab--active')
    expect(w.find('.zc-sql-block').text()).toContain('第 2/2 条')
    expect(w.vm.current.resultIndex).toBe(1)
  })

  it('边界：点结果页签不触发任何 localStorage 写入', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    await viewResult(w, 1)
    await viewResult(w, 0)
    // 没有持久字段发生变化：点了就写盘是多余的同步 IO
    expect(saved()).toBeNull()
    expect(storage.map.size).toBe(0)
  })

  it('边界：落盘形状只有 id/name/sql，结果与停留位一起被白名单削掉', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    await viewResult(w, 1)
    w.unmount() // 卸载时的兜底 flush 也不能漏一个 resultIndex
    expect(Object.keys(saved().tabs[0]).sort()).toEqual(['id', 'name', 'sql'])
  })

  it('边界：重执行后结果变少，原先停在尾页不渲染空白面板', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    await viewResult(w, 1)
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [rowsResult(0, 'SELECT 9', [{ n: 9 }])] } })
    await w.find('.zc-sql-editor').setValue('SELECT 9')
    await w.find('.zc-sql-run').trigger('click')
    await flushPromises()
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.find('.zc-sql-block').text()).toContain('第 1/1 条')
    expect(w.find('.zc-sql-block').text()).toContain('SELECT 9')
    expect(w.vm.current.resultIndex).toBe(0)
  })

  it('边界：selectResult 越界（负数与超出结果数）钳回 0，不把面板渲染空', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    await w.vm.selectResult(99)
    expect(w.vm.activeResult).toBe(0)
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    await w.vm.selectResult(-1)
    expect(w.vm.activeResult).toBe(0)
    expect(w.find('.zc-sql-block').text()).toContain('第 1/2 条')
  })

  it('边界：未执行的结果页签点得开（skipped 不等于不可交互）', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      errorResult(0, 'SELECT * FROM nope', 'relation "nope" does not exist'),
      skippedResult(1, 'SELECT 2'),
    ] } })
    const w = await runWith('SELECT * FROM nope; SELECT 2')
    const second = await viewResult(w, 1)
    expect(second.classes()).toContain('zc-sql-block--skipped')
    expect(second.text()).toContain('未执行')
    expect(w.findAll('.zc-sql-rtab')[1].attributes('disabled')).toBeUndefined()
  })

  it('边界：结果页签不是可关闭的工作现场（没有 ×）', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.findAll('.zc-sql-rtab-close')).toHaveLength(0)
    expect(w.findAll('.zc-sql-rtab button')).toHaveLength(0)
  })
})

// ---- Ticket 006：选区优先执行 + 格式化。
// 桩上的选区是这一票唯一可控的“编辑器输入”：先框选再点按钮，断言控制台把哪几条送去执行、
// 格式化写回了哪个区间。格式化的具体输出由 utils/sqlFormat.test.js 逐字钉住，这里用 fmt() 现算。
async function mountTyped(doc, stubs = {}) {
  const w = mountConsole({}, stubs)
  await flushPromises()
  await typeInto(w, doc)
  return w
}

async function runSelected(doc, from, to, stubs = {}) {
  const w = await mountTyped(doc, stubs)
  selectIn(doc, from, to)
  await w.find('.zc-sql-run').trigger('click')
  await flushPromises()
  return w
}

async function formatDoc(doc, from, to, stubs = {}) {
  const w = await mountTyped(doc, stubs)
  selectIn(doc, from, to)
  await w.find('.zc-sql-format').trigger('click')
  await flushPromises()
  return w
}

describe('SqlConsole 选区优先执行（Ticket 006 / US-10）', () => {
  it('有选区只把选中的那一条送去执行，脚本原文一字不动', async () => {
    const doc = 'SELECT 1; SELECT 2; SELECT 3'
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [rowsResult(0, 'SELECT 2', [{ n: 2 }])] } })
    const at = doc.indexOf('SELECT 2')
    const w = await runSelected(doc, at, at + 'SELECT 2'.length)
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledTimes(1)
    expect(mocks.api.dbSqlBatch.mock.calls[0][2]).toEqual(['SELECT 2'])
    // “只跑选区”不是“把没选的都删掉”：执行不改写用户脚本
    expect(w.find('.zc-sql-editor').element.value).toBe(doc)
    // 块头分母也跟着选区走，不能报成「第 1/3 条」
    expect(w.find('.zc-sql-summary').text()).toBe('共 1 条，成功 1')
  })

  it('选区跨两条：只执行这两条，第三条不进去', async () => {
    const doc = 'SELECT 1; SELECT 2; SELECT 3;'
    const w = await runSelected(doc, doc.indexOf('SELECT 2'), doc.length)
    expect(mocks.api.dbSqlBatch.mock.calls[0][2]).toEqual(['SELECT 2', 'SELECT 3'])
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
  })

  it('边界：光标塌成一个点（from === to）算无选区，照旧跑全段', async () => {
    const doc = 'SELECT 1; SELECT 2; SELECT 3'
    await runSelected(doc, 5, 5)
    expect(mocks.api.dbSqlBatch.mock.calls[0][2]).toEqual(['SELECT 1', 'SELECT 2', 'SELECT 3'])
  })

  it('边界：选区只有空白 → 不发请求，提示没有可执行的语句', async () => {
    const doc = 'SELECT 1;    ; SELECT 2'
    const at = doc.indexOf('    ')
    const w = await runSelected(doc, at, at + 4)
    expect(mocks.api.dbSqlBatch).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalledWith('没有可执行的语句')
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(0)
  })

  it('边界：选区只有注释 → 不发请求，提示没有可执行的语句', async () => {
    const doc = 'SELECT 1;\n-- 待确认的改动\nSELECT 2;'
    const at = doc.indexOf('--')
    await runSelected(doc, at, doc.indexOf('\n', at))
    expect(mocks.api.dbSqlBatch).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalledWith('没有可执行的语句')
  })

  it('选区内含写语句：确认弹框只列选中的这两条，整批仍只确认一次', async () => {
    const doc = "SELECT 1; UPDATE widgets SET name = 'a'; DELETE FROM widgets"
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      affectedResult(0, "UPDATE widgets SET name = 'a'", 1),
      affectedResult(1, 'DELETE FROM widgets', 3),
    ] } })
    const w = await runSelected(doc, doc.indexOf('UPDATE'), doc.length)
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    const content = mocks.antd.Modal.confirm.mock.calls[0][0].content
    expect(content).not.toContain('SELECT 1')
    expect(content).toContain("UPDATE widgets SET name = 'a'")
    expect(content).toContain('DELETE FROM widgets')
    expect(mocks.api.dbSqlBatch.mock.calls[0][2]).toEqual(["UPDATE widgets SET name = 'a'", 'DELETE FROM widgets'])
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
  })

  it('边界：编辑器不暴露选区通道（纯 textarea 桩）时回落全段执行', async () => {
    const w = await runSelected('SELECT 1; SELECT 2', 0, 9, { SqlEditor: TEXTAREA_ONLY_STUB })
    expect(mocks.api.dbSqlBatch.mock.calls[0][2]).toEqual(['SELECT 1', 'SELECT 2'])
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
  })

  it('编辑器里按 Ctrl+Enter（execute 事件）走同一个执行入口', async () => {
    const w = await mountTyped('SELECT 1; SELECT 2')
    w.findComponent({ name: 'SqlEditor' }).vm.$emit('execute')
    await flushPromises()
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledTimes(1)
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
  })
})

describe('SqlConsole 格式化（Ticket 006 / US-02）', () => {
  it('工具栏有「格式化」入口，提示语说清选中只执行选区', async () => {
    const w = mountConsole()
    await flushPromises()
    expect(w.find('.zc-sql-format').text()).toContain('格式化')
    expect(w.find('.zc-sql-hint').text()).toContain('选区')
  })

  // 验收 3 的两个快捷键得能在界面上被发现（悬停提示），不能只写在工单里。
  // 只断言 title 里含键位，不锁死大小写与空格写法，免得改文案就红。
  it('执行与格式化按钮带快捷键悬停提示', async () => {
    const w = mountConsole()
    await flushPromises()
    expect(String(w.find('.zc-sql-run').attributes('title'))).toMatch(/Enter/i)
    expect(String(w.find('.zc-sql-format').attributes('title'))).toMatch(/Shift\+F/i)
  })

  it('无选区点格式化：整段重排写回实际区间', async () => {
    const doc = 'select id, name from widgets where n = 1'
    await formatDoc(doc, 0, 0)
    expect(stubReplace).toHaveLength(1)
    expect(stubReplace[0].from).toBe(0)
    expect(stubReplace[0].to).toBe(doc.length)
    expect(stubReplace[0].insert).toBe(fmt(doc))
  })

  it('有选区只重排选中片段，未选的前后一字不动', async () => {
    const doc = 'SELECT 1;\nselect   2 from widgets;\nSELECT 3;'
    const at = doc.indexOf('select   2')
    const end = doc.indexOf(';\nSELECT 3') + 1
    const core = doc.slice(at, end)
    const w = await formatDoc(doc, at, end)
    expect(stubReplace[0].from).toBe(at)
    expect(stubReplace[0].to).toBe(end)
    expect(stubReplace[0].insert).toBe(fmt(core))
    const next = w.find('.zc-sql-editor').element.value
    expect(next).toBe(doc.slice(0, at) + fmt(core) + doc.slice(end))
    expect(next.startsWith('SELECT 1;\n')).toBe(true)
    expect(next.endsWith('\nSELECT 3;')).toBe(true)
  })

  it('格式化后选区落在重排出来的那段上，不把光标丢回文首', async () => {
    const doc = 'SELECT 1;\nselect   2 from widgets;\nSELECT 3;'
    const at = doc.indexOf('select   2')
    const end = doc.indexOf(';\nSELECT 3') + 1
    const core = doc.slice(at, end)
    const w = await formatDoc(doc, at, end)
    expect(stubReplace[0].sel).toEqual({ start: at, end: at + fmt(core).length })
    const next = w.find('.zc-sql-editor').element.value
    expect(next.slice(stubReplace[0].sel.start, stubReplace[0].sel.end)).toBe(fmt(core))
  })

  it('边界：无选区时不重设选区（sel 传 undefined，光标留在原处）', async () => {
    await formatDoc('select 1', 3, 3)
    expect(stubReplace[0].sel).toBeNull()
  })

  it('边界：未闭合引号的选区保持原文并 warning，一个字都不写回', async () => {
    const doc = "SELECT 1; select 'unterminated"
    const at = doc.indexOf('select ')
    const w = await formatDoc(doc, at, doc.length)
    expect(stubReplace).toHaveLength(0)
    expect(mocks.antd.message.warning).toHaveBeenCalled()
    expect(mocks.antd.message.info).not.toHaveBeenCalled()
    expect(w.find('.zc-sql-editor').element.value).toBe(doc)
  })

  it('边界：空白文档点格式化只 info 不写回，不写盘', async () => {
    const w = await formatDoc('   \n  ', 0, 0)
    expect(stubReplace).toHaveLength(0)
    expect(mocks.antd.message.info).toHaveBeenCalledWith('没有可格式化的内容')
    expect(w.find('.zc-sql-editor').element.value).toBe('   \n  ')
    expect(saved()).toBeNull()
  })

  it('边界：选区终点越过文档末尾（钳到文末）照旧格式化选中部分', async () => {
    const doc = 'SELECT 1;\nselect 2 from widgets'
    const at = doc.indexOf('select 2')
    const w = await formatDoc(doc, at, doc.length + 99)
    expect(stubReplace[0].to).toBe(doc.length)
    expect(w.find('.zc-sql-editor').element.value).toBe('SELECT 1;\n' + fmt(doc.slice(at)))
  })

  it('边界：重复格式化幂等，第二次不再改变文本', async () => {
    const w = await formatDoc('select  1,2 from widgets', 0, 0)
    const first = w.find('.zc-sql-editor').element.value
    expect(first).toBe(fmt('select  1,2 from widgets'))
    await w.find('.zc-sql-format').trigger('click')
    await flushPromises()
    expect(stubReplace).toHaveLength(2)
    expect(stubReplace[1].insert).toBe(stubReplace[0].insert)
    expect(w.find('.zc-sql-editor').element.value).toBe(first)
  })

  it('边界：格式化不清空结果区，页签与停留位都在', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    await viewResult(w, 1)
    await typeInto(w, 'select 1; select 2')
    await w.find('.zc-sql-format').trigger('click')
    await flushPromises()
    expect(w.findAll('.zc-sql-rtab')).toHaveLength(2)
    expect(w.find('.zc-sql-block').text()).toContain('第 2/2 条')
    expect(w.vm.current.resultIndex).toBe(1)
  })

  it('边界：退化桩没有写回通道时走 v-model 整段替换，不抛错', async () => {
    const doc = 'select 1'
    const w = await formatDoc(doc, 0, 0, { SqlEditor: TEXTAREA_ONLY_STUB })
    expect(stubReplace).toHaveLength(0)
    expect(w.find('.zc-sql-editor').element.value).toBe(fmt(doc))
  })

  it('格式化后的文本照常进本地存档（不是只改了显示）', async () => {
    const doc = 'select 1'
    const w = await formatDoc(doc, 0, 0)
    expect(saved()).toBeNull() // 防抖未到点
    w.unmount() // 卸载兜底 flush
    expect(saved().tabs[0].sql).toBe(fmt(doc))
  })

  it('编辑器里按 Ctrl+Shift+F（format 事件）走同一个格式化入口', async () => {
    const doc = 'select 1'
    const w = await mountTyped(doc)
    w.findComponent({ name: 'SqlEditor' }).vm.$emit('format')
    await flushPromises()
    expect(stubReplace).toHaveLength(1)
    expect(w.find('.zc-sql-editor').element.value).toBe(fmt(doc))
  })
})
