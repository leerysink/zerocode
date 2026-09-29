// @vitest-environment happy-dom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { Comment, h } from 'vue'
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
  // a-modal 桩：没开就不渲染内容（否则“未操作就有弹框”这类残留测不出来）；
  // wrap-class-name 不声明为 prop，靠 fallthrough 落到根节点上，与抽屉 root-class-name 同一口径。
  'a-modal': { props: ['open', 'title'], emits: ['update:open'], render() {
    if (!this.open) return null
    return h('div', { class: 'zc-db-modal' }, [
      h('div', { class: 'zc-db-modal-title' }, this.title),
      h('div', { class: 'zc-db-modal-body' }, this.$slots.default?.()),
      h('div', { class: 'zc-db-modal-footer' }, this.$slots.footer?.()),
    ])
  } },
}

beforeEach(() => {
  vi.clearAllMocks()
  // 默认让确认框直接执行 onOk（等价于用户点「执行」）；测「取消」的用例自行覆盖。
  // 不重建的话，上一个用例改成 vi.fn() 会泄漏给后面的用例。
  mocks.antd.Modal.confirm = vi.fn(({ onOk }) => onOk && onOk())
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

// ---------- Ticket 010: 数据网格行末固定列「编辑」+ 弹框（入口从底部搬到行末）----------

/**
 * 数据网格桩：忠实还原 antd v4 的单元格优先级——先调 #bodyCell，插槽返回全是注释
 * （v-if 未命中）时**回落**到 customRender / 原值，见 ant-design-vue/es/_util/vnode.js
 * 的 customRenderSlot + ensureValidVNode。
 * 默认 a-table 桩只看 dataSource、走不到插槽；已有的 bodyCellTable 桩走插槽但没有回落，
 * 拿它测会把「行号列被插槽清空」这类误实现测成通过，所以单开一个。
 */
const gridTable = {
  props: ['columns', 'dataSource'],
  render() {
    const cellContent = (column, record, text, index) => {
      const slotted = this.$slots.bodyCell
        ? this.$slots.bodyCell({ column, record, text, index })
        : null
      const valid = Array.isArray(slotted) ? slotted.some((child) => child && child.type !== Comment) : !!slotted
      if (valid) return slotted
      return column.customRender ? column.customRender({ text, record, index, column }) : String(text ?? '')
    }
    return h('table', { class: 'zc-db-grid' }, h('tbody', (this.dataSource || []).map((record, ri) =>
      h('tr', { key: ri }, (this.columns || []).map((c) =>
        h('td', { key: c.dataIndex ?? c.key }, cellContent(c, record, record[c.dataIndex], ri)))))))
  },
}

const mountGrid = (extraStubs = {}) => mount(DbAdminView, {
  global: { stubs: { ...STUBS, 'a-table': gridTable, ...extraStubs } },
})

/** 选默认表 → 点第 rowIndex 行的行末编辑按钮。 */
async function openEditModal(w, rowIndex = 0) {
  await flushPromises()
  await w.find('.zc-tree-node').trigger('click')
  await flushPromises()
  await dataGrid(w).findAll('.zc-db-row-edit')[rowIndex].trigger('click')
  await flushPromises()
  return w
}

/**
 * 数据网格在所有 .zc-db-grid 里排最前（数据页签先于结构/查询页签渲染）。
 * 不拿容器限定就会把结构页的表头一并数进来（样式轮就踩过这个坑：格子数从 4 变 7）。
 */
const dataGrid = (w) => w.findAll('.zc-db-grid')[0]

it('选中有主键表拉数据，点行末编辑开弹框，保存走二次确认后执行 SQL', async () => {
  mocks.api.dbSqlExec.mockResolvedValue({ data: { kind: 'affected', affected: 1 } })
  const w = mountGrid()
  await openEditModal(w)
  expect(mocks.api.dbTableData).toHaveBeenCalledWith('1', 'dev', 'widgets', expect.anything())
  expect(w.find('.zc-db-modal').exists()).toBe(true)
  const save = w.find('.zc-db-edit-save')
  expect(save.exists()).toBe(true)
  await save.trigger('click')
  await flushPromises()
  expect(mocks.antd.Modal.confirm).toHaveBeenCalledWith(expect.objectContaining({
    content: expect.stringContaining('UPDATE'),
  }))
  expect(mocks.api.dbSqlExec).toHaveBeenCalledWith('1', 'dev', expect.stringContaining('UPDATE'))
  // 保存成功 → 弹框自己关掉，并重拉数据
  expect(w.find('.zc-db-modal').exists()).toBe(false)
  expect(mocks.api.dbTableData).toHaveBeenCalledTimes(2)
})

it('无主键表禁用行内编辑但仍可浏览数据', async () => {
  mocks.api.dbTableSchema.mockResolvedValueOnce({ data: { columns: [{ name: 'id', type: 'integer', nullable: false, default: null }], primary_key: [], indexes: [] } })
  const w = mountGrid()
  await flushPromises()
  await w.find('.zc-tree-node').trigger('click')
  await flushPromises()
  expect(w.find('.zc-db-edit-disabled').exists()).toBe(true)
  // 编辑列照常渲染（不整列消失，避免有无主键时列宽跳动），但按钮禁用
  const btns = dataGrid(w).findAll('.zc-db-row-edit')
  expect(btns).toHaveLength(1)
  expect(btns[0].attributes('disabled')).toBeDefined()
  expect(w.find('.zc-db-edit-save').exists()).toBe(false)
  // 无主键不影响拉数据/渲染网格（视图可浏览）
  expect(mocks.api.dbTableData).toHaveBeenCalledWith('1', 'dev', 'widgets', expect.anything())
  expect(w.find('.zc-db-grid').exists()).toBe(true)
  // 格子顺序：行号 / id / name / 操作；数据格仍正常渲染（插槽只抢了操作列）
  expect(dataGrid(w).findAll('tbody tr td')[2].text()).toBe('a')
})

describe('Ticket 010 数据网格行末编辑列与弹框', () => {
  it('边界：操作列声明 fixed right，且插槽没抢走行号列与数据列', async () => {
    let gridCols = null
    const capTable = {
      props: ['columns', 'dataSource'],
      render() {
        if (this.columns?.[0]?.key === '__row_no') gridCols = this.columns
        return gridTable.render.call(this)
      },
    }
    const w = mountGrid({ 'a-table': capTable })
    await flushPromises()
    await w.find('.zc-tree-node').trigger('click')
    await flushPromises()
    const actions = gridCols[gridCols.length - 1]
    expect(actions.key).toBe('__actions')
    expect(actions.fixed).toBe('right')
    expect(typeof actions.width).toBe('number')
    // #bodyCell 里只写 __actions 分支，其他列靠 antd 的回落语义继续渲染
    const cells = dataGrid(w).findAll('tbody tr:first-child td')
    expect(cells).toHaveLength(4)
    expect(cells[0].text()).toBe('1') // 行号列 customRender
    expect(cells[1].text()).toBe('1') // id
    expect(cells[2].text()).toBe('a') // name
    expect(cells[3].find('.zc-db-row-edit').exists()).toBe(true)
  })

  it('点第 2 行的编辑，弹框里是第 2 行的数据且标题带表名', async () => {
    mocks.api.dbTableData.mockResolvedValue({ data: { columns: ['id', 'name'], rows: [{ id: 1, name: 'a' }, { id: 2, name: 'b' }], total: 2, truncated: false } })
    const w = mountGrid()
    await openEditModal(w, 1)
    expect(w.find('.zc-db-modal-title').text()).toContain('widgets')
    const inputs = w.findAll('.zc-db-modal .zc-db-edit-field input')
    expect(inputs[0].element.value).toBe('2')
    expect(inputs[1].element.value).toBe('b')
  })

  it('边界：弹框里主键列 disabled 只读展示，其余列可编辑', async () => {
    const w = mountGrid()
    await openEditModal(w)
    const labels = w.findAll('.zc-db-modal .zc-db-edit-field label').map((l) => l.text())
    expect(labels).toEqual(['id', 'name'])
    const inputs = w.findAll('.zc-db-modal .zc-db-edit-field input')
    expect(inputs[0].attributes('disabled')).toBeDefined()
    expect(inputs[1].attributes('disabled')).toBeUndefined()
  })

  it('边界：进页面/翻页/刷新都不自动弹编辑框（原「首行默认进编辑态」已删）', async () => {
    mocks.api.dbTableData.mockResolvedValue({ data: { columns: ['id', 'name'], rows: [{ id: 1, name: 'a' }], total: 120, truncated: false } })
    let goPage2 = null
    const pagTable = {
      props: ['columns', 'dataSource', 'pagination'],
      render() {
        const p = this.pagination
        if (p && typeof p.onChange === 'function') goPage2 = () => p.onChange(2, p.pageSize)
        return gridTable.render.call(this)
      },
    }
    const w = mountGrid({ 'a-table': pagTable })
    await flushPromises()
    await w.find('.zc-tree-node').trigger('click')
    await flushPromises()
    expect(w.find('.zc-db-modal').exists()).toBe(false)
    expect(w.vm.editOpen).toBe(false)
    expect(w.vm.editRow).toBe(null)
    goPage2()
    await flushPromises()
    await w.find('.zc-db-refresh').trigger('click')
    await flushPromises()
    expect(w.find('.zc-db-modal').exists()).toBe(false)
    expect(w.vm.editOpen).toBe(false)
  })

  it('边界：编辑的是副本，取消后表格里的原值未被改写也不打后端', async () => {
    const w = mountGrid()
    await openEditModal(w)
    await w.findAll('.zc-db-modal .zc-db-edit-field input')[1].setValue('changed')
    await flushPromises()
    expect(dataGrid(w).findAll('tbody tr')[0].findAll('td')[2].text()).toBe('a')
    await w.find('.zc-db-edit-cancel').trigger('click')
    await flushPromises()
    expect(w.find('.zc-db-modal').exists()).toBe(false)
    expect(mocks.api.dbSqlExec).not.toHaveBeenCalled()
    // 重新打开是服务端原值，上一轮的脏草稿不残留
    await dataGrid(w).findAll('.zc-db-row-edit')[0].trigger('click')
    await flushPromises()
    expect(w.findAll('.zc-db-modal .zc-db-edit-field input')[1].element.value).toBe('a')
  })

  it('边界：二次确认框被取消 → 不打后端、弹框还开着、改动不丢', async () => {
    mocks.antd.Modal.confirm = vi.fn() // 不执行 onOk 等价于用户点「取消」
    const w = mountGrid()
    await openEditModal(w)
    await w.findAll('.zc-db-modal .zc-db-edit-field input')[1].setValue('keep me')
    await w.find('.zc-db-edit-save').trigger('click')
    await flushPromises()
    expect(mocks.api.dbSqlExec).not.toHaveBeenCalled()
    expect(w.find('.zc-db-modal').exists()).toBe(true)
    expect(w.findAll('.zc-db-modal .zc-db-edit-field input')[1].element.value).toBe('keep me')
  })

  it('边界：全列都是主键时保存只给 warning，不打后端也不关弹框', async () => {
    mocks.api.dbTableData.mockResolvedValue({ data: { columns: ['id'], rows: [{ id: 1 }], total: 1, truncated: false } })
    const w = mountGrid()
    await openEditModal(w)
    await w.find('.zc-db-edit-save').trigger('click')
    await flushPromises()
    expect(mocks.antd.message.warning).toHaveBeenCalledWith(expect.stringContaining('无可编辑列'))
    expect(mocks.antd.Modal.confirm).not.toHaveBeenCalled()
    expect(mocks.api.dbSqlExec).not.toHaveBeenCalled()
    expect(w.find('.zc-db-modal').exists()).toBe(true)
  })

  it('边界：无主键时禁用提示挂在包裹 span 上（disabled 的 button 不吃 hover）', async () => {
    mocks.api.dbTableSchema.mockResolvedValueOnce({ data: { columns: [{ name: 'id', type: 'integer', nullable: false, default: null }], primary_key: [], indexes: [] } })
    const w = mountGrid()
    await flushPromises()
    await w.find('.zc-tree-node').trigger('click')
    await flushPromises()
    const wrap = dataGrid(w).find('.zc-db-row-edit-wrap')
    expect(wrap.exists()).toBe(true)
    expect(wrap.attributes('title')).toContain('无主键')
    expect(w.find('.zc-db-row-edit').attributes('disabled')).toBeDefined()
  })

  it('边界：openEdit 在无主键表上直接 return，绕过禁用态调用也弹不出来', async () => {
    mocks.api.dbTableSchema.mockResolvedValueOnce({ data: { columns: [{ name: 'id', type: 'integer', nullable: false, default: null }], primary_key: [], indexes: [] } })
    const w = mountGrid()
    await flushPromises()
    await w.find('.zc-tree-node').trigger('click')
    await flushPromises()
    w.vm.openEdit({ id: 1, name: 'a' })
    await flushPromises()
    expect(w.vm.editOpen).toBe(false)
    expect(w.vm.editRow).toBe(null)
    expect(w.find('.zc-db-modal').exists()).toBe(false)
  })

  it('边界：弹框带 wrap-class-name 样式锚点，漏了就等于 portal 出去没样式', async () => {
    const w = mountGrid()
    await openEditModal(w)
    expect(w.find('[wrap-class-name="zc-db-edit-modal"]').exists()).toBe(true)
    expect(w.find('[wrap-class-name="zc-db-edit-modal"] .zc-db-edit-save').exists()).toBe(true)
  })

  // 两列栅格至少需要 280×2 + 列间距 16 + body 左右 padding 28 ≈ 604px，
  // a-modal 默认 520px 永远只解出一条轨道，13 列的宽表会排成一长条（浏览器实测踩过）。
  it('边界：弹框显式给宽，不给就装不下两列栅格', async () => {
    const w = mountGrid()
    await openEditModal(w)
    expect(Number(w.find('.zc-db-modal').attributes('width'))).toBeGreaterThanOrEqual(604)
  })

  it('边界：编辑途中换环境（dev→prod）弹框立即关闭，不留上一环境的草稿', async () => {
    const w = mountGrid()
    await openEditModal(w)
    await w.findAll('.zc-db-modal .zc-db-edit-field input')[1].setValue('draft')
    await w.findAll('.zc-db-env button')[1].trigger('click')
    await flushPromises()
    expect(w.vm.editOpen).toBe(false)
    expect(w.find('.zc-db-modal').exists()).toBe(false)
  })

  it('边界：行点击入口已删除（custom-row 不再传给表格，点行没有副作用）', async () => {
    let seenAttrs = null
    const attrTable = {
      props: ['columns', 'dataSource'],
      render() {
        seenAttrs = { ...this.$attrs }
        return gridTable.render.call(this)
      },
    }
    const w = mountGrid({ 'a-table': attrTable })
    await flushPromises()
    await w.find('.zc-tree-node').trigger('click')
    await flushPromises()
    expect(seenAttrs['custom-row']).toBeUndefined()
    expect(seenAttrs.customRow).toBeUndefined()
    await dataGrid(w).findAll('tbody tr td')[0].trigger('click')
    await flushPromises()
    expect(w.vm.editOpen).toBe(false)
    expect(w.vm.editRow).toBe(null)
    expect(w.find('.zc-db-modal').exists()).toBe(false)
  })
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

  it('边界：portal 到 body 的四个作用域锚点类名都还在文件里', () => {
    for (const anchor of ['.zc-db', '.zc-db-drawer', '.zc-db-dd', '.zc-db-edit-modal']) {
      expect(lessSource).toContain(anchor)
    }
  })

  // 样式轮容易留死 CSS：入口搬走了但旧规则还在，不报错也不会被看到，只能源码闸拦。
  it('边界：旧的底部编辑面板样式已清，行末按钮样式已补', () => {
    expect(lessCode).not.toContain('.zc-db-edit-panel')
    expect(lessCode).not.toContain('.zc-db-edit-title')
    expect(lessCode).toMatch(/\.zc-db-row-edit\s*\{/)
    expect(lessCode).toMatch(/\.zc-db-row-edit:disabled\s*\{/)
  })

  // 固定列与表头底色分不开的话，横向滚动时最右列表头会比同行浅一格（实测踩过）。
  it('边界：固定列表头跟普通表头同底色，弹框有窄窗口兜底', () => {
    const headFix = lessCode.match(/\.ant-table-thead[^{]*th\.ant-table-cell-fix-right\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(headFix).toContain('--zc-db-chrome')
    expect(headFix).not.toContain('--zc-db-panel')
    expect(lessCode).toMatch(/\.ant-modal\s*\{[^}]*max-width:\s*calc\(100vw/)
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
