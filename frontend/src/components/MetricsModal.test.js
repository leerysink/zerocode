// @vitest-environment happy-dom
// 资源监控弹框（重设计）渲染契约：分组 2×2 卡片（运行时 CPU/内存 + 存储 DB/工作区）、
// 每卡 当前/均值/峰值 三值、内联 SVG 精致曲线要素（面积 path + 折线 + 末端当前点 + 时间刻度轴）、
// 停止/未采集空态、窗口切换重取 history。a-spin 给最小桩，断言落在真实渲染文本与元素上。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { appMetricsCurrent: vi.fn(), appMetricsHistory: vi.fn() },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

import MetricsModal from './MetricsModal.vue'

const STUBS = {
  'a-spin': { name: 'a-spin', props: ['spinning'],
    render() { return h('div', { class: 'stub-spin' }, this.$slots.default?.()) } },
  'a-empty': { name: 'a-empty', props: ['description'],
    render() { return h('div', { class: 'stub-empty' }, String(this.description ?? '')) } },
}

const TS = '2026-09-28T10:00:00+00:00'
const CURRENT = {
  cpu: { value: 20, ts: TS },
  memory: { value: 300 * 1024 * 1024, ts: TS },
  db_size: { value: 10 * 1024 * 1024, ts: TS },
  workspace_size: { value: 2 * 1024 * 1024 * 1024, ts: TS },
  prod_status: 'running',
}
// 每类给 3 个点：cpu 均值 20 / 峰值 30，用于断言三值与曲线要素。
const HIST = {
  cpu: [{ ts: '2026-09-28T08:00:00+00:00', value: 10 },
        { ts: '2026-09-28T09:00:00+00:00', value: 30 },
        { ts: '2026-09-28T10:00:00+00:00', value: 20 }],
  memory: [{ ts: '2026-09-28T08:00:00+00:00', value: 100 },
           { ts: '2026-09-28T10:00:00+00:00', value: 300 }],
  db_size: [{ ts: '2026-09-28T08:00:00+00:00', value: 8 },
            { ts: '2026-09-28T10:00:00+00:00', value: 10 }],
  workspace_size: [{ ts: '2026-09-28T08:00:00+00:00', value: 2 },
                   { ts: '2026-09-28T10:00:00+00:00', value: 3 }],
}

async function mountModal(current = CURRENT, history = HIST) {
  mocks.api.appMetricsCurrent.mockResolvedValue({ data: current })
  mocks.api.appMetricsHistory.mockResolvedValue({ data: history })
  const wrapper = mount(MetricsModal, {
    props: { app: { id: 7, name: '甲' } }, global: { stubs: STUBS },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('MetricsModal 分组与三值', () => {
  it('呈现运行时与存储两组、四个指标卡片', async () => {
    const wrapper = await mountModal()
    const text = wrapper.text()
    expect(text).toContain('运行时')
    expect(text).toContain('存储')
    expect(wrapper.findAll('.zc-metric-card')).toHaveLength(4)
    for (const label of ['CPU', '内存', '数据库磁盘', '工作区磁盘']) {
      expect(text).toContain(label)
    }
  })

  it('每卡显示 当前/均值/峰值，且从历史点算出均值与峰值', async () => {
    const wrapper = await mountModal()
    const cpu = wrapper.findAll('.zc-metric-card')[0]
    expect(cpu.text()).toContain('当前')
    expect(cpu.text()).toContain('均值')
    expect(cpu.text()).toContain('峰值')
    expect(cpu.text()).toContain('20.0 %')    // 当前
    expect(cpu.text()).toContain('30.0 %')    // 峰值 = 三点最高
  })

  it('打开时按 app.id 拉 current 与默认 24h 的 history', async () => {
    await mountModal()
    expect(mocks.api.appMetricsCurrent).toHaveBeenCalledWith(7)
    expect(mocks.api.appMetricsHistory).toHaveBeenCalledWith(7, '24h')
  })
})

describe('MetricsModal 精致曲线要素', () => {
  it('每个有数据的指标渲染 SVG：面积 path + 折线 + 末端当前点 + 时间刻度轴', async () => {
    const wrapper = await mountModal()
    const card = wrapper.findAll('.zc-metric-card')[0]
    expect(card.find('svg').exists()).toBe(true)
    expect(card.find('.zc-chart-area').exists()).toBe(true)
    expect(card.find('.zc-chart-line').exists()).toBe(true)
    expect(card.find('.zc-chart-cursor').exists()).toBe(true)
    expect(card.find('.zc-axis-start').text()).not.toBe('')
    expect(card.find('.zc-axis-end').text()).not.toBe('')
  })
})

describe('MetricsModal 空态', () => {
  it('停止态：cpu/memory 当前显示「无实时数据」，但窗口内均值/峰值仍展示', async () => {
    const stopped = { ...CURRENT, cpu: null, memory: null, prod_status: 'stopped' }
    const wrapper = await mountModal(stopped)
    const cpu = wrapper.findAll('.zc-metric-card')[0]
    expect(cpu.text()).toContain('无实时数据')
    expect(cpu.text()).toContain('均值')   // 历史仍在
  })

  it('未采集的磁盘类显示「尚未采集」', async () => {
    const sparse = { ...CURRENT, workspace_size: null }
    const wrapper = await mountModal(sparse, { ...HIST, workspace_size: [] })
    const ws = wrapper.findAll('.zc-metric-card')[3]
    expect(ws.text()).toContain('尚未采集')
  })
})

describe('MetricsModal 窗口切换', () => {
  it('点窗口标签按新 window 重取 history', async () => {
    const wrapper = await mountModal()
    const tab = wrapper.findAll('.zc-window-tab').find((b) => b.text() === '7d')
    await tab.trigger('click')
    await flushPromises()
    expect(mocks.api.appMetricsHistory).toHaveBeenLastCalledWith(7, '7d')
  })

  it('切窗 history 失败：窗口标签回滚到原值，不把旧曲线错标成新窗口', async () => {
    mocks.api.appMetricsCurrent.mockResolvedValue({ data: CURRENT })
    mocks.api.appMetricsHistory.mockResolvedValueOnce({ data: HIST }).mockRejectedValueOnce(new Error('boom'))
    const wrapper = mount(MetricsModal, { props: { app: { id: 7 } }, global: { stubs: STUBS } })
    await flushPromises()   // 默认 24h 成功
    const tab7 = wrapper.findAll('.zc-window-tab').find((b) => b.text() === '7d')
    await tab7.trigger('click')
    await flushPromises()   // 7d 失败 → 回滚
    const active = wrapper.findAll('.zc-window-tab').find((b) => b.classes().includes('active'))
    expect(active.text()).toBe('24h')
  })

  it('首屏 current 失败：显示「暂无数据」而非谎报未采集', async () => {
    mocks.api.appMetricsCurrent.mockRejectedValue(new Error('boom'))
    mocks.api.appMetricsHistory.mockResolvedValue({ data: HIST })
    const wrapper = mount(MetricsModal, { props: { app: { id: 7 } }, global: { stubs: STUBS } })
    await flushPromises()
    expect(wrapper.find('.stub-empty').text()).toContain('暂无数据')
    expect(wrapper.find('.zc-metric-card').exists()).toBe(false)
  })
})
