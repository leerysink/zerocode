// @vitest-environment happy-dom
// Task 10：PublishHistory——发布历史表行为契约（04 §4.4 回滚警示 / FR4-08 删记录）。
// a-table 替身按 columns×dataSource 展开并回调 #bodyCell 插槽，断言落在渲染结果上。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { appRecords: vi.fn(), deleteRecord: vi.fn() },
  antd: {
    message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn() },
  },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

import PublishHistory from './PublishHistory.vue'

const CUR = 'b20260921120000'
const RECORDS = [
  { id: 3, app_id: 7, kind: 'publish', version: CUR, status: 'success', step: 'record',
    started_at: '2026-09-21T12:00:00', finished_at: '2026-09-21T12:01:00', error: null,
    detail: { sqlApplied: ['V20260921_01_add_todo.sql'], durationMs: 61000 } },
  { id: 2, app_id: 7, kind: 'publish', version: 'b20260920100000', status: 'running', step: 'build',
    started_at: '2026-09-20T10:00:00', finished_at: null, error: null, detail: {} },
  { id: 1, app_id: 7, kind: 'rollback', version: 'b20260919090000', status: 'failed', step: 'build',
    started_at: '2026-09-19T09:00:00', finished_at: '2026-09-19T09:00:40', error: 'build boom',
    detail: { durationMs: 40000 } },
]
const APP = { id: 7, name: '待办', slug: 'todo8', current_version: CUR, prod_port: 8210 }

const STUBS = {
  'a-table': {
    name: 'a-table', props: ['columns', 'dataSource'],
    render() {
      const cells = (record) => this.columns.map((column) =>
        this.$slots.bodyCell
          ? this.$slots.bodyCell({ column, record, text: record[column.dataIndex] })
          : h('span', String(record[column.dataIndex] ?? '')))
      return h('div', { class: 'stub-table', 'data-cols': String(this.columns.length) },
        (this.dataSource || []).map((r) => h('div', { class: 'stub-row', key: r.id }, cells(r))))
    },
  },
  'a-popconfirm': {
    name: 'a-popconfirm', props: ['title'],
    render() {
      return h('span', { class: 'stub-pop' }, [
        this.$slots.default?.(),
        h('button', { class: 'stub-pop-ok', onClick: () => this.$emit('confirm') }),
      ])
    },
  },
  'a-button': {
    name: 'a-button', props: ['disabled'],
    render() {
      return h('button', { class: 'stub-btn', disabled: !!this.disabled,
        onClick: (e) => this.$emit('click', e) }, this.$slots.default?.())
    },
  },
  'a-spin': { name: 'a-spin', render() { return h('span', 'spin') } },
  'a-empty': { name: 'a-empty', props: ['description'], render() { return h('p', this.description) } },
}

async function mountHistory(records = RECORDS) {
  mocks.api.appRecords.mockResolvedValue({ data: records })
  const wrapper = mount(PublishHistory, { props: { app: APP }, global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

beforeEach(() => vi.clearAllMocks())

describe('PublishHistory 列表渲染', () => {
  it('六列 × 三行；状态点 success/failed/running 各自成形', async () => {
    const wrapper = await mountHistory()
    expect(wrapper.find('.stub-table').attributes('data-cols')).toBe('6')
    expect(wrapper.findAll('.stub-row')).toHaveLength(3)
    expect(wrapper.findAll('.dot-success')).toHaveLength(1)
    expect(wrapper.findAll('.dot-failed')).toHaveLength(1)
    const runDot = wrapper.find('.dot-running')
    expect(runDot.exists()).toBe(true)
    expect(wrapper.findAll('.stub-row')[1].text()).toContain('镜像构建')   // running 带 step 名
  })

  it('耗时优先取 detail.durationMs', async () => {
    const wrapper = await mountHistory()
    expect(wrapper.text()).toContain('61s')
    expect(wrapper.text()).toContain('40s')
  })

  it('空态引导文案', async () => {
    const wrapper = await mountHistory([])
    expect(wrapper.text()).toContain('还没有发布记录')
  })

  it('挂载即按 app.id 拉记录', async () => {
    await mountHistory()
    expect(mocks.api.appRecords).toHaveBeenCalledWith(7)
  })

  it('failed+step=interrupted 收口为「已中断」（终审 Minor-1：与普通失败可分辨）', async () => {
    const wrapper = await mountHistory([
      { id: 9, app_id: 7, kind: 'publish', version: 'b20260921080000', status: 'failed',
        step: 'interrupted', started_at: '2026-09-21T08:00:00', finished_at: null,
        error: '平台重启中断', detail: {} },
      ...RECORDS,
    ])
    expect(wrapper.findAll('.stub-row')[0].text()).toContain('已中断')
    expect(wrapper.find('.dot-interrupted').exists()).toBe(true)
    expect(wrapper.findAll('.dot-failed')).toHaveLength(1)   // 普通失败行不受影响
  })
})

describe('PublishHistory 回滚入口', () => {
  it('仅「publish 且非当前版本」行的回滚按钮可点', async () => {
    const wrapper = await mountHistory()
    const btns = wrapper.findAll('.zc-btn-rollback')
    expect(btns).toHaveLength(3)
    expect(btns[0].attributes('disabled')).toBeDefined()   // 当前版本
    expect(btns[1].attributes('disabled')).toBeUndefined() // 旧版 publish 可回滚
    expect(btns[2].attributes('disabled')).toBeDefined()   // rollback 类型不可再回滚
  })

  it('确认文案逐字（04 §4.4）；确认后 emit rollback 携带版本', async () => {
    const wrapper = await mountHistory()
    await wrapper.findAll('.zc-btn-rollback')[1].trigger('click')
    const arg = mocks.antd.Modal.confirm.mock.calls[0][0]
    expect(arg.content).toContain('回滚只恢复程序版本，数据库结构与数据保持当前状态；'
      + '若新版本改过表结构，旧程序可能读写异常，请联系 AI 生成兼容处理。')
    arg.onOk()
    expect(wrapper.emitted('rollback')).toEqual([['b20260920100000']])
  })
})

describe('PublishHistory 记录操作', () => {
  it('删除记录：Popconfirm 确认后 deleteRecord(app_id, rid) 并重拉', async () => {
    const wrapper = await mountHistory()
    mocks.api.deleteRecord.mockResolvedValue({ data: { ok: true } })
    mocks.api.appRecords.mockResolvedValue({ data: [] })   // 重拉后为空即证明刷新
    await wrapper.findAll('.stub-pop-ok')[0].trigger('click')
    await flushPromises()
    expect(mocks.api.deleteRecord).toHaveBeenCalledWith(7, 3)
    expect(wrapper.text()).toContain('还没有发布记录')
  })

  it('查看日志：展示 sqlApplied 与 error；无记录时提示无', async () => {
    const wrapper = await mountHistory()
    await wrapper.findAll('.zc-btn-log')[2].trigger('click')
    let text = wrapper.find('.zc-rec-detail').text()
    expect(text).toContain('build boom')
    await wrapper.findAll('.zc-btn-log')[0].trigger('click')
    text = wrapper.find('.zc-rec-detail').text()
    expect(text).toContain('V20260921_01_add_todo.sql')
    expect(text).not.toContain('build boom')
  })
})
