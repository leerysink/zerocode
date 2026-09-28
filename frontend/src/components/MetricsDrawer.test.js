// @vitest-environment happy-dom
// Ticket 001–004：MetricsDrawer 渲染契约——各类指标当前值格式化（CPU 百分比 / 内存与磁盘字节）、
// 缺失 kind 走「尚未采集」、每类历史 sparkline 独立渲染、窗口切换统一驱动重取 history。
// a-spin / a-empty 给最小桩，断言落在真实渲染文本与属性上。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { appMetricsCurrent: vi.fn(), appMetricsHistory: vi.fn() },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

import MetricsDrawer from './MetricsDrawer.vue'

const STUBS = {
  'a-spin': { name: 'a-spin', render() { return h('div', { class: 'stub-spin' }) } },
  'a-empty': { name: 'a-empty', props: ['description'],
    render() { return h('div', { class: 'stub-empty' }, this.description) } },
}

const TS = '2026-09-28T10:00:00+00:00'
// db_size 缺席：Ticket 004 后端不返该键，前端须显示「尚未采集」而非报错/0。
const CURRENT = {
  cpu: { value: 12.5, ts: TS },
  memory: { value: 5 * 1024 * 1024, ts: TS },
  workspace_size: { value: 2048, ts: TS },
  prod_status: 'running',
}

async function mountDrawer(current = CURRENT, history = {}) {
  mocks.api.appMetricsCurrent.mockResolvedValue({ data: current })
  mocks.api.appMetricsHistory.mockResolvedValue({ data: history })
  const wrapper = mount(MetricsDrawer, {
    props: { app: { id: 7, name: '甲' } }, global: { stubs: STUBS },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('MetricsDrawer 当前值', () => {
  it('CPU 按百分比、内存与工作区按字节格式化，prod 态中文化', async () => {
    const wrapper = await mountDrawer()
    expect(wrapper.text()).toContain('12.5 %')
    expect(wrapper.text()).toContain('5.0 MB')
    expect(wrapper.text()).toContain('2.0 KB')
    expect(wrapper.text()).toContain('运行中')
  })

  it('响应缺失的 kind（db_size）显示「尚未采集」而非 0 或报错', async () => {
    const wrapper = await mountDrawer()
    expect(wrapper.text()).toContain('尚未采集')
    expect(wrapper.text()).not.toMatch(/\b0 B\b/)
  })

  it('打开时按当前 app.id 拉 current 与默认 24h 的 history', async () => {
    await mountDrawer()
    expect(mocks.api.appMetricsCurrent).toHaveBeenCalledWith(7)
    expect(mocks.api.appMetricsHistory).toHaveBeenCalledWith(7, '24h')
  })
})

describe('MetricsDrawer 历史折线', () => {
  it('每类 ≥2 点各渲染一条 polyline', async () => {
    const wrapper = await mountDrawer(CURRENT, {
      cpu: [{ ts: 'a', value: 1 }, { ts: 'b', value: 3 }, { ts: 'c', value: 2 }],
      memory: [],
      workspace_size: [{ ts: 'a', value: 100 }, { ts: 'b', value: 200 }],
    })
    const lines = wrapper.findAll('polyline')
    expect(lines).toHaveLength(2)
    expect(lines[0].attributes('points')).toMatch(/,/)
  })

  it('点不足 2 个的 kind 不画 polyline', async () => {
    const wrapper = await mountDrawer(CURRENT, {
      cpu: [{ ts: 'a', value: 1 }],
      workspace_size: [{ ts: 'a', value: 100 }, { ts: 'b', value: 200 }],
    })
    expect(wrapper.findAll('polyline')).toHaveLength(1)
  })

  it('点窗口标签统一按新 window 重取 history', async () => {
    const wrapper = await mountDrawer()
    const tab = wrapper.findAll('.zc-window-tab').find((b) => b.text() === '7d')
    await tab.trigger('click')
    await flushPromises()
    expect(mocks.api.appMetricsHistory).toHaveBeenLastCalledWith(7, '7d')
  })
})
