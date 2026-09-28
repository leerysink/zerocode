// @vitest-environment happy-dom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { dbTables: vi.fn(), dbTableSchema: vi.fn(), dbTableData: vi.fn(), dbSqlExec: vi.fn(), dbSqlBatch: vi.fn(), dbAudit: vi.fn() },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn(({ onOk }) => onOk && onOk()) } },
  route: { params: { id: '1' } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => ({ ...mocks.antd, default: { message: mocks.antd.message, Modal: mocks.antd.Modal } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))

import DbAdminView from './DbAdminView.vue'
import SqlConsole from '../components/SqlConsole.vue'

const STUBS = {
  'a-segmented': { props: ['value', 'options'], emits: ['update:value'], render() {
    return h('div', { class: 'zc-db-env' }, (this.options || []).map((o) =>
      h('button', { onClick: () => this.$emit('update:value', o.value) }, o.value))) } },
  'a-tree': { props: ['treeData'], emits: ['select'], render() {
    return h('div', { class: 'zc-db-tree' }, (this.treeData || []).map((n) =>
      h('div', { class: 'zc-tree-node', onClick: () => this.$emit('select', [n.key]) }, n.title))) } },
  'a-tabs': { render() { return h('div', this.$slots.default?.()) } },
  'a-tab-pane': { props: ['tab'], render() { return h('div', { class: 'stub-pane' }, this.$slots.default?.()) } },
  'a-table': { props: ['columns', 'dataSource'], render() {
    return h('table', { class: 'zc-db-table' }, h('tbody', (this.dataSource || []).map((r, i) =>
      h('tr', { key: i }, Object.values(r).map((v) => h('td', String(v))))))) } },
  'a-alert': { props: ['message'], render() { return h('div', { class: 'zc-db-prod-warn' }, this.message) } },
  'a-button': { render() { return h('button', { class: 'zc-db-btn' }, this.$slots.default?.()) } },
  'a-input': { props: ['value', 'disabled'], emits: ['change', 'update:value'], render() {
    return h('input', { class: 'zc-db-input', value: this.value ?? '', disabled: !!this.disabled,
      onInput: (e) => this.$emit('change', { target: { value: e.target.value } }) }) } },
  // SqlConsole 自带编辑器（CodeMirror 在 happy-dom 起不来），本视图只测"接线"：挂载 + 传参。
  SqlEditor: { template: '<textarea />' },
  'a-drawer': { props: ['open'], render() { return this.open ? h('div', this.$slots.default?.()) : null } },
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.api.dbTables.mockResolvedValue({ data: { tables: [{ name: 'widgets', type: 'BASE TABLE' }] } })
  mocks.api.dbTableSchema.mockResolvedValue({ data: { columns: [{ name: 'id', type: 'integer', nullable: false, default: null }], primary_key: ['id'], indexes: [] } })
  mocks.api.dbTableData.mockResolvedValue({ data: { columns: ['id', 'name'], rows: [{ id: 1, name: 'a' }], total: 1, truncated: false } })
})

it('挂载拉表并渲染树节点', async () => {
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  expect(mocks.api.dbTables).toHaveBeenCalledWith('1', 'dev')
  expect(w.find('.zc-tree-node').text()).toContain('widgets')
})

it('切到 prod 重新拉表并显示警示条', async () => {
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.findAll('.zc-db-env button')[1].trigger('click')
  await flushPromises()
  expect(mocks.api.dbTables).toHaveBeenCalledWith('1', 'prod')
  expect(w.find('.zc-db-prod-warn').exists()).toBe(true)
})

// ---------- Task 10: 数据 tab 行内编辑二次确认 + 无主键禁编辑 ----------
it('选中有主键表拉数据，编辑保存走二次确认后执行 SQL', async () => {
  mocks.api.dbSqlExec.mockResolvedValue({ data: { kind: 'affected', affected: 1 } })
  mocks.antd.Modal.confirm = vi.fn(({ onOk }) => onOk())
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-tree-node').trigger('click')
  await flushPromises()
  expect(mocks.api.dbTableData).toHaveBeenCalledWith('1', 'dev', 'widgets', expect.anything())
  const save = w.find('.zc-db-edit-save')
  expect(save.exists()).toBe(true)
  await save.trigger('click')
  await flushPromises()
  expect(mocks.antd.Modal.confirm).toHaveBeenCalled()
  expect(mocks.api.dbSqlExec).toHaveBeenCalledWith('1', 'dev', expect.stringContaining('UPDATE'))
})

it('无主键表禁用行内编辑但仍可浏览数据', async () => {
  mocks.api.dbTableSchema.mockResolvedValueOnce({ data: { columns: [{ name: 'id', type: 'integer', nullable: false, default: null }], primary_key: [], indexes: [] } })
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-tree-node').trigger('click')
  await flushPromises()
  expect(w.find('.zc-db-edit-disabled').exists()).toBe(true)
  expect(w.find('.zc-db-edit-save').exists()).toBe(false)
  // 无主键不影响拉数据/渲染网格（视图可浏览）
  expect(mocks.api.dbTableData).toHaveBeenCalledWith('1', 'dev', 'widgets', expect.anything())
  expect(w.find('.zc-db-table').exists()).toBe(true)
})

// ---------- Ticket 002: 查询控制台接线（SQL 执行用例已迁往 SqlConsole.test.js）----------
it('查询页签挂 SqlConsole 并把 appId/env/tables 透传', async () => {
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  const console_ = w.findComponent(SqlConsole)
  expect(console_.exists()).toBe(true)
  expect(console_.props('appId')).toBe('1')
  expect(console_.props('env')).toBe('dev')
  expect(console_.props('tables')).toEqual([{ name: 'widgets', type: 'BASE TABLE' }])
})

it('边界：切到 prod 后 SqlConsole 收到的是 prod', async () => {
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.findAll('.zc-db-env button')[1].trigger('click')
  await flushPromises()
  expect(w.findComponent(SqlConsole).props('env')).toBe('prod')
})

it('打开审计抽屉拉操作历史', async () => {
  mocks.api.dbAudit.mockResolvedValue({ data: { items: [{ id: 1, op_type: 'select', status: 'success', env: 'dev' }] } })
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-db-audit-btn').trigger('click')
  await flushPromises()
  expect(mocks.api.dbAudit).toHaveBeenCalledWith('1', expect.anything())
  expect(w.find('.zc-db-audit-drawer').exists()).toBe(true)
})

// ---------- Ticket 004: 审计过滤 + 顶栏刷新 ----------
it('审计抽屉按环境过滤重拉', async () => {
  mocks.api.dbAudit.mockResolvedValue({ data: { items: [] } })
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-db-audit-btn').trigger('click')
  await flushPromises()
  const envSel = w.find('.zc-db-audit-env')
  expect(envSel.exists()).toBe(true)
  await envSel.setValue('prod')
  await envSel.trigger('change')
  await flushPromises()
  expect(mocks.api.dbAudit).toHaveBeenLastCalledWith('1', expect.objectContaining({ env: 'prod' }))
})

it('顶栏刷新重拉当前表数据', async () => {
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-tree-node').trigger('click')
  await flushPromises()
  expect(w.find('.zc-db-refresh').exists()).toBe(true)
  mocks.api.dbTableData.mockClear()
  await w.find('.zc-db-refresh').trigger('click')
  await flushPromises()
  expect(mocks.api.dbTableData).toHaveBeenCalledWith('1', 'dev', 'widgets', expect.anything())
})
