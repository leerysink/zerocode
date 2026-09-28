// @vitest-environment happy-dom
// Ticket 002：查询控制台示踪弹——一个框写 `SELECT 1; SELECT 2`，点执行穿
// 「前端拆分 → 批量端点 → 逐条结果块渲染」这一整根竖线。
//
// CodeMirror 6 依赖真实测量与布局，happy-dom 里起不来，故本票把 SqlEditor 桩成
// textarea：控制台的逻辑（切段、确认口径、调用批量端点、渲染）全部在桩外可测。
// 语句级失败与 skipped 归 003、假分号归 004、多页签归 005、选区优先与格式化归 006。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { dbSqlBatch: vi.fn() },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn(({ onOk }) => onOk && onOk()) } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => ({ ...mocks.antd, default: { message: mocks.antd.message, Modal: mocks.antd.Modal } }))

import SqlConsole from './SqlConsole.vue'

const rowsResult = (index, sql, rows, extra = {}) => ({
  index, op: 'select', sql, status: 'ok', duration_ms: 3,
  kind: 'rows', columns: ['n'], rows, truncated: false, ...extra,
})
const affectedResult = (index, sql, affected) => ({
  index, op: 'insert', sql, status: 'ok', duration_ms: 5, kind: 'affected', affected,
})

// SqlEditor 桩：textarea 双向绑定 + 暴露 getSelectionText（本票恒空，006 才消费选区）
const EDITOR_STUB = {
  props: ['modelValue'],
  emits: ['update:modelValue'],
  render() {
    return h('textarea', { class: 'zc-sql-editor', value: this.modelValue ?? '',
      onInput: (e) => this.$emit('update:modelValue', e.target.value) })
  },
  methods: { getSelectionText: () => '' },
}

const STUBS = {
  SqlEditor: EDITOR_STUB,
  'a-button': { props: ['disabled', 'loading'], render() {
    return h('button', { class: 'zc-sql-btn', disabled: !!this.disabled }, this.$slots.default?.()) } },
  'a-table': { props: ['columns', 'dataSource'], render() {
    return h('table', { class: 'zc-sql-table' }, h('tbody', (this.dataSource || []).map((r, i) =>
      h('tr', { key: i }, Object.values(r).map((v) => h('td', String(v))))))) } },
}

function mountConsole(props = {}) {
  return mount(SqlConsole, {
    props: { appId: '1', env: 'dev', tables: [{ name: 'widgets' }], ...props },
    global: { stubs: STUBS },
  })
}

async function runWith(text, props = {}) {
  const w = mountConsole(props)
  await flushPromises()
  if (text !== null) await w.find('.zc-sql-editor').setValue(text)
  await w.find('.zc-sql-run').trigger('click')
  await flushPromises()
  return w
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.antd.Modal.confirm = vi.fn(({ onOk }) => onOk && onOk())
  mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
    rowsResult(0, 'SELECT 1', [{ n: 1 }]), rowsResult(1, 'SELECT 2', [{ n: 2 }]),
  ] } })
})

describe('SqlConsole 批量执行（主流程）', () => {
  it('两条 SELECT 拆成数组送批量端点，并渲染两个结果块', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledTimes(1)
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledWith('1', 'dev', ['SELECT 1', 'SELECT 2'])
    const blocks = w.findAll('.zc-sql-block')
    expect(blocks).toHaveLength(2)
    expect(blocks[0].text()).toContain('第 1/2 条')
    expect(blocks[1].text()).toContain('第 2/2 条')
    expect(blocks[0].findAll('.zc-sql-table')).toHaveLength(1)
  })

  it('结果块头部带 op 与耗时，让每条各自可辨认', async () => {
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.findAll('.zc-sql-block')[0].text()).toContain('select')
    expect(w.findAll('.zc-sql-block')[0].text()).toContain('3ms')
  })

  it('混合段按序渲染：写显示影响行数、读显示表格', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      affectedResult(0, "INSERT INTO widgets VALUES (1,'a')", 1),
      rowsResult(1, 'SELECT * FROM widgets', [{ id: 1 }, { id: 2 }]),
    ] } })
    const w = await runWith("INSERT INTO widgets VALUES (1,'a'); SELECT * FROM widgets")
    const blocks = w.findAll('.zc-sql-block')
    expect(blocks[0].text()).toContain('影响 1 行')
    expect(blocks[0].find('.zc-sql-table').exists()).toBe(false)
    expect(blocks[1].find('.zc-sql-table').exists()).toBe(true)
    expect(blocks[1].findAll('.zc-sql-table tr')).toHaveLength(2)
  })

  it('含非只读语句时整批只确认一次，确认后执行', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      affectedResult(0, 'UPDATE widgets SET name=1', 1),
      affectedResult(1, 'DELETE FROM widgets', 1),
    ] } })
    const w = await runWith('UPDATE widgets SET name=1; DELETE FROM widgets')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledTimes(1)
    expect(w.findAll('.zc-sql-block')).toHaveLength(2)
  })

  it('纯读段不弹确认', async () => {
    await runWith('SELECT 1; SELECT 2')
    expect(mocks.antd.Modal.confirm).not.toHaveBeenCalled()
  })

  it('截断的 SELECT 结果块展示截断提示', async () => {
    mocks.api.dbSqlBatch.mockResolvedValue({ data: { results: [
      rowsResult(0, 'SELECT 1', [{ n: 1 }], { truncated: true }), rowsResult(1, 'SELECT 2', [{ n: 2 }]),
    ] } })
    const w = await runWith('SELECT 1; SELECT 2')
    expect(w.findAll('.zc-sql-block')[0].find('.zc-sql-truncated').exists()).toBe(true)
    expect(w.findAll('.zc-sql-block')[1].find('.zc-sql-truncated').exists()).toBe(false)
  })
})

describe('SqlConsole 边界', () => {
  it('取消二次确认则一条都不执行', async () => {
    mocks.antd.Modal.confirm = vi.fn() // 弹了但用户取消（不调 onOk）
    const w = await runWith('UPDATE widgets SET name=1')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    expect(mocks.api.dbSqlBatch).not.toHaveBeenCalled()
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
  })

  it('边界：空输入点执行不打后端并提示', async () => {
    const w = await runWith('')
    expect(mocks.api.dbSqlBatch).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalled()
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
    expect(w.findAll('.zc-sql-block')).toHaveLength(1)
    expect(w.findAll('.zc-sql-block')[0].text()).toContain('第 1/1 条')
  })

  it('边界：执行打到当前 env（prod 亦是同一端点）', async () => {
    await runWith('SELECT 1', { env: 'prod' })
    expect(mocks.api.dbSqlBatch).toHaveBeenCalledWith('1', 'prod', ['SELECT 1'])
  })

  it('边界：切换 env 清空上一次结果，不拿 dev 的结果糊 prod', async () => {
    const w = await runWith('SELECT 1')
    expect(w.findAll('.zc-sql-block')).toHaveLength(2)
    await w.setProps({ env: 'prod' })
    await flushPromises()
    expect(w.findAll('.zc-sql-block')).toHaveLength(0)
  })

  it('边界：请求失败错误上屏且不残留旧结果', async () => {
    mocks.api.dbSqlBatch.mockRejectedValue({ response: { data: { detail: '目标库不可达' } } })
    const w = await runWith('SELECT 1')
    expect(mocks.antd.message.error).toHaveBeenCalledWith('目标库不可达')
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
