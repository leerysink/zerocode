// @vitest-environment happy-dom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { dbTables: vi.fn(), dbTableSchema: vi.fn(), dbTableData: vi.fn(), dbSqlExec: vi.fn(), dbAudit: vi.fn() },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn(({ onOk }) => onOk && onOk()) } },
  route: { params: { id: '1' } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => ({ ...mocks.antd, default: { message: mocks.antd.message, Modal: mocks.antd.Modal } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))

import DbAdminView from './DbAdminView.vue'

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
  'a-drawer': { props: ['open'], render() { return this.open ? h('div', this.$slots.default?.()) : null } },
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.api.dbTables.mockResolvedValue({ data: { tables: [{ name: 'widgets', type: 'BASE TABLE' }] } })
  mocks.api.dbTableSchema.mockResolvedValue({ data: { columns: [{ name: 'id', type: 'integer', nullable: false, default: null }], primary_key: ['id'], indexes: [] } })
  mocks.api.dbTableData.mockResolvedValue({ data: { columns: ['id'], rows: [{ id: 1 }], total: 1, truncated: false } })
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
