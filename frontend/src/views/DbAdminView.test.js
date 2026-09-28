// @vitest-environment happy-dom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
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
  // a-select 桩：仍渲染**原生 select**，既有的 setValue / trigger('change') 用法不用改；
  // class 靠 fallthrough 落到根节点上，所以 .zc-db-audit-env 依旧能找到。
  'a-select': { props: ['value', 'options'], emits: ['update:value', 'change'], render() {
    return h('select', { value: this.value ?? '', onChange: (e) => {
      this.$emit('update:value', e.target.value)
      this.$emit('change', e.target.value)
    } }, (this.options || []).map((o) => h('option', { value: o.value }, o.label))) } },
  'a-tag': { props: ['color'], render() { return h('span', { class: 'zc-db-tag' }, this.$slots.default?.()) } },
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

// ---------- 样式轮：状态栏 / 空态 / 行号 / 审计单元格格式化 ----------

/** 能走通 #bodyCell 插槽的 a-table 桩（默认桩只看 dataSource，走不到列定制渲染）。 */
const bodyCellTable = {
  props: ['columns', 'dataSource'],
  render() {
    return h('table', { class: 'zc-db-table' }, h('tbody', (this.dataSource || []).map((r, ri) => h('tr', { key: ri },
      (this.columns || []).map((c) => h('td', { key: c.dataIndex ?? c.key },
        this.$slots.bodyCell
          ? this.$slots.bodyCell({ column: c, record: r, text: r[c.dataIndex] })
          : String(r[c.dataIndex] ?? '')))))))
  },
}

describe('DbAdminView 样式轮边界', () => {
  it('状态栏：未选表说「未选择表」而不是「共 0 行」', async () => {
    const w = mount(DbAdminView, { global: { stubs: STUBS } })
    await flushPromises()
    expect(w.find('.zc-db-status').text()).toContain('未选择表')
    expect(w.find('.zc-db-status').text()).toContain('表 1 张')
    expect(w.find('.zc-db-status').text()).not.toContain('共 0 行')
  })

  it('状态栏：选中表后带表名与行列信息', async () => {
    const w = mount(DbAdminView, { global: { stubs: STUBS } })
    await flushPromises()
    await w.find('.zc-tree-node').trigger('click')
    await flushPromises()
    expect(w.find('.zc-db-status').text()).toContain('widgets · 共 1 行 · 第 1 页')
  })

  it('边界：选中表但 total 为 0 时仍是表名口径（共 0 行），不误报未选择', async () => {
    mocks.api.dbTableData.mockResolvedValue({ data: { columns: ['id'], rows: [], total: 0, truncated: false } })
    const w = mount(DbAdminView, { global: { stubs: STUBS } })
    await flushPromises()
    await w.find('.zc-tree-node').trigger('click')
    await flushPromises()
    expect(w.find('.zc-db-status').text()).toContain('共 0 行')
    expect(w.find('.zc-db-status').text()).not.toContain('未选择表')
  })

  it('边界：库内一张表都没有时左树走空态，不渲染空树', async () => {
    mocks.api.dbTables.mockResolvedValue({ data: { tables: [] } })
    const w = mount(DbAdminView, { global: { stubs: STUBS } })
    await flushPromises()
    expect(w.find('.zc-db-left-empty').text()).toContain('该库暂无表')
    expect(w.find('.zc-db-tree').exists()).toBe(false)
    expect(w.find('.zc-db-left-count').text()).toBe('0')
    expect(w.find('.zc-db-status').text()).toContain('表 0 张')
  })

  it('边界：prod 才给根节点挂 is-prod 警示类，dev 不染色', async () => {
    const w = mount(DbAdminView, { global: { stubs: STUBS } })
    await flushPromises()
    expect(w.find('.zc-db.is-prod').exists()).toBe(false)
    await w.findAll('.zc-db-env button')[1].trigger('click')
    await flushPromises()
    expect(w.find('.zc-db.is-prod').exists()).toBe(true)
  })

  it('边界：行号列跨页连续，第 2 页首行是 51 而不是 1', async () => {
    mocks.api.dbTableData.mockResolvedValue({ data: { columns: ['id'], rows: [{ id: 51 }], total: 120, truncated: false } })
    let gridCols = null
    const w = mount(DbAdminView, { global: { stubs: {
      ...STUBS,
      'a-table': {
        props: ['columns', 'pagination'],
        render() {
          // 只拿数据网格（其他页签的表不关注行号列），避免被结构/审计表的 columns 覆盖
          if (!this.columns || this.columns[0]?.key !== '__row_no') return h('table', { class: 'zc-db-table' })
          gridCols = this.columns
          const p = this.pagination || {}
          return h('button', { class: 'stub-page-2', onClick: () => p.onChange && p.onChange(2, p.pageSize) })
        },
      },
    } } })
    await flushPromises()
    await w.find('.zc-tree-node').trigger('click')
    await flushPromises()
    expect(gridCols[0].customRender({ index: 0 })).toBe(1)
    await w.find('.stub-page-2').trigger('click')
    await flushPromises()
    expect(gridCols[0].customRender({ index: 0 })).toBe(51)
    expect(gridCols[0].customRender({ index: 4 })).toBe(55)
  })

  it('审计行：非 success 状态出「失败」，空列出 -，时间压到秒', async () => {
    mocks.api.dbAudit.mockResolvedValue({ data: { items: [
      { id: 1, ts: '2026-09-28T10:11:12.123456+08:00', env: 'dev', op_type: 'insert', status: 'success',
        duration_ms: 0, target_table: 'widgets', sql_text: 'INSERT INTO widgets VALUES (0,\'\')', error: null },
      { id: 2, ts: null, env: 'prod', op_type: 'ddl', status: 'error',
        duration_ms: null, target_table: null, sql_text: 'DROP TABLE x', error: 'relation "x" does not exist' },
      { id: 3, ts: '刚刚', env: 'dev', op_type: 'select', status: 'unknown',
        duration_ms: 12, target_table: '', sql_text: 'SELECT 1', error: '' },
    ] } })
    const w = mount(DbAdminView, { global: { stubs: { ...STUBS, 'a-table': bodyCellTable } } })
    await flushPromises()
    await w.find('.zc-db-audit-btn').trigger('click')
    await flushPromises()
    const rows = w.findAll('.zc-db-audit-drawer tbody tr')
    expect(rows).toHaveLength(3)
    const cellsOf = (i) => rows[i].findAll('td').map((td) => td.text())
    // 带微秒 + 时区的 ISO 只留到秒；null 给 - 不编造时间
    expect(cellsOf(0)[0]).toBe('2026-09-28 10:11:12')
    expect(cellsOf(1)[0]).toBe('-')
    // 非 ISO 形状原样输出，不拿 Invalid Date 糊上去
    expect(cellsOf(2)[0]).toBe('刚刚')
    // 后端只写 success / error，但前端的口径是「非 success 即失败」，未知值不默默当成功
    expect(cellsOf(0)[3]).toBe('成功')
    expect(cellsOf(1)[3]).toBe('失败')
    expect(cellsOf(2)[3]).toBe('失败')
    // 耗时 0ms 是合法值，不能被空值占位吞成 -
    expect(cellsOf(0)[4]).toBe('0')
    expect(cellsOf(1)[4]).toBe('-')
    // 空串与 null 同口径给 -
    expect(cellsOf(1)[5]).toBe('-')
    expect(cellsOf(2)[5]).toBe('-')
    expect(cellsOf(0)[7]).toBe('-')
    expect(cellsOf(1)[7]).toBe('relation "x" does not exist')
  })

  it('边界：审计空结果集只渲染表头不报错，也不塞一条假记录', async () => {
    mocks.api.dbAudit.mockResolvedValue({ data: { items: [] } })
    const w = mount(DbAdminView, { global: { stubs: { ...STUBS, 'a-table': bodyCellTable } } })
    await flushPromises()
    await w.find('.zc-db-audit-btn').trigger('click')
    await flushPromises()
    expect(w.findAll('.zc-db-audit-drawer tbody tr')).toHaveLength(0)
  })

  // 样式轮的作用域锚点：抽屉与下拉面板都 portal 到 body，样式全靠这两个类名才够得到；
  // 类名一旦被误删，表现是「静默回到全局旧主色」而不是报错，所以必须有断言钉住。
  it('边界：portal 出去的抽屉与两个下拉都带着样式类，不带就等于样式漏了', async () => {
    mocks.api.dbAudit.mockResolvedValue({ data: { items: [] } })
    const w = mount(DbAdminView, { global: { stubs: STUBS } })
    await flushPromises()
    await w.find('.zc-db-audit-btn').trigger('click')
    await flushPromises()
    // root-class-name 落在抽屉根节点上（内容体是它的子节点），不是 .zc-db-audit-drawer 本身
    expect(w.find('[root-class-name="zc-db-drawer"]').exists()).toBe(true)
    expect(w.find('[root-class-name="zc-db-drawer"] .zc-db-audit-drawer').exists()).toBe(true)
    expect(w.find('.zc-db-audit-env').attributes('popup-class-name')).toBe('zc-db-dd')
    expect(w.find('.zc-db-audit-op').attributes('popup-class-name')).toBe('zc-db-dd')
  })

  it('边界：没开抽屉时不渲染过滤器，也没有残留的下拉样式类', async () => {
    const w = mount(DbAdminView, { global: { stubs: STUBS } })
    await flushPromises()
    expect(w.find('.zc-db-audit-env').exists()).toBe(false)
    expect(w.find('.zc-db-audit-op').exists()).toBe(false)
  })
})

// ---------- 样式轮：单源样式文件的防回归闸 ----------
// happy-dom 没有布局引擎，颜色/层叠这类纯视觉属性本来靠浏览器 getComputedStyle 实测；
// 但「旧主色有没有被硬编码回来」「portal 块的层叠保险还在不在」这两件事可以直接读源码断言，
// 成本极低且能在 CI 里拦住，不至于等上线后静默变回旧蓝灰才发现。
// 用 fs 直接读原文：`?raw` 在 vitest 里会被 css 插件接走返空串，import.meta.url 在 happy-dom 下也不是
// file 协议；改成候选路径探测，从 frontend 或从仓库根跑 vitest 都能命中。
const lessPath = ['src/styles/db-admin.less', 'frontend/src/styles/db-admin.less']
  .map((p) => resolve(process.cwd(), p))
  .find((p) => existsSync(p))
const lessSource = lessPath ? readFileSync(lessPath, 'utf8') : ''

describe('DB 操作台样式单源 db-admin.less', () => {
  // 注释里大量提到旧主色（写明“原本是什么、为什么换”），剔掉注释再断言，否则误杀注释。
  const lessCode = lessSource.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')

  it('边界：样式文件里不出现全局旧主色字面量，主色只能有变量那一处', () => {
    // 路径探测失败时 lessSource 是空串，先钉住非空再往下断言，免得“不含旧色”这种 not 断言空跑通过
    expect(lessSource.length).toBeGreaterThan(0)
    expect(lessCode).not.toMatch(/#5b7a9d/i)
    expect(lessCode).not.toMatch(/91,\s*122,\s*157/)
    expect(lessSource).toMatch(/--zc-db-accent:\s*#4a6d94/)
  })

  it('边界：portal 到 body 的三个作用域锚点类名都还在文件里', () => {
    for (const anchor of ['.zc-db', '.zc-db-drawer', '.zc-db-dd']) {
      expect(lessSource).toContain(anchor)
    }
  })

  // antd cssinjs 用 :where(hash) 前缀不计权重，单类根与它特异性打平，胜负只能赌注入顺序。
  it('边界：下拉面板块带 .ant-select-dropdown 层叠保险，少了就可能被 antd 反超', () => {
    expect(lessSource).toMatch(/\.zc-db-dd\.ant-select-dropdown/)
  })

  it('边界：样式全收在作用域内，不允许声明全局 colorPrimary 覆盖', () => {
    expect(lessSource).not.toMatch(/^\s*:root\s*\{/m)
    expect(lessSource).not.toMatch(/^\s*body\s*\{/m)
  })
})
