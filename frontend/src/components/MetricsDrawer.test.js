// @vitest-environment happy-dom
// Ticket 001：MetricsDrawer 当前值渲染契约——工作区磁盘字节数格式化、未采集占位、prod 态映射。
// a-spin / a-empty 给最小桩，断言落在真实渲染文本上。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { appMetricsCurrent: vi.fn() },
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

async function mountDrawer(data) {
  mocks.api.appMetricsCurrent.mockResolvedValue({ data })
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
  it('有工作区指标时按 MB 格式化展示', async () => {
    const wrapper = await mountDrawer({
      workspace_size: { value: 5 * 1024 * 1024, ts: '2026-09-28T10:00:00+00:00' },
      prod_status: 'running',
    })
    expect(wrapper.text()).toContain('5.0 MB')
    expect(wrapper.text()).toContain('运行中')
  })

  it('workspace_size 为 null 时显示「尚未采集」而非 0', async () => {
    const wrapper = await mountDrawer({ workspace_size: null, prod_status: 'stopped' })
    expect(wrapper.text()).toContain('尚未采集')
    expect(wrapper.text()).not.toMatch(/\b0 B\b/)
  })

  it('按调用 appMetricsCurrent 传入当前 app.id', async () => {
    await mountDrawer({ workspace_size: null, prod_status: 'stopped' })
    expect(mocks.api.appMetricsCurrent).toHaveBeenCalledWith(7)
  })
})
