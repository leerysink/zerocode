// @vitest-environment happy-dom
// 预览状态轮询的节奏契约（票 002 / spec §5.3）。
// 直接测 composable 不绕组件：节奏是它自己的职责，组件只负责把结果画出来。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { mount, enableAutoUnmount } from '@vue/test-utils'
import { useDevStatus } from './useDevStatus'

vi.mock('../api', () => ({ getApp: vi.fn() }))
import { getApp } from '../api'

const statusOf = (s) => ({ data: { id: 12, status_dev: s, preview_url: 'http://x/' } })

// useDevStatus 依赖组件生命周期（onBeforeUnmount），套一个最小宿主组件才能挂上
const Host = (options = {}) => ({
  setup() {
    const api = useDevStatus(ref(12), options)
    options.onApi?.(api)
    return () => null
  },
})

// visibilitychange 监听挂在共享的 document 上：不自动卸载，前面几个测试的实例会一起被
// 后面那次 dispatch 唤醒，把请求数叠成假象（实测 2+4=6）。
enableAutoUnmount(afterEach)

beforeEach(() => {
  vi.useFakeTimers()
  getApp.mockReset().mockResolvedValue(statusOf('stopped'))
})
afterEach(() => { vi.useRealTimers() })

describe('轮询节奏', () => {
  it('稳定态每 5s 一次', async () => {
    mount(Host())
    await vi.advanceTimersByTimeAsync(15000)
    const after = getApp.mock.calls.length
    await vi.advanceTimersByTimeAsync(15000)
    expect(getApp.mock.calls.length - after).toBe(3)
  })

  it('starting 期间加密到 2s 一次', async () => {
    getApp.mockResolvedValue(statusOf('starting'))
    mount(Host())
    await vi.advanceTimersByTimeAsync(6000)
    // 首 tick + 2s/4s/6s 三次 = 4；若还停在 5s 档只会是 2 次
    expect(getApp.mock.calls.length).toBe(4)
  })

  it('回到 running 后降回 5s', async () => {
    getApp.mockResolvedValue(statusOf('starting'))
    mount(Host())
    await vi.advanceTimersByTimeAsync(6000)
    getApp.mockResolvedValue(statusOf('running'))
    await vi.advanceTimersByTimeAsync(2000)
    const at = getApp.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(getApp.mock.calls.length - at).toBe(2)
  })

  // 聊天页常驻在后台标签里。不停摆就是拿一个没人看的页面每分钟白打 12 次数据库。
  it('页面隐藏时不打，切回来立即补一次', async () => {
    const hidden = { value: false }
    Object.defineProperty(document, 'hidden', { get: () => hidden.value, configurable: true })

    mount(Host())
    await vi.advanceTimersByTimeAsync(5000)
    const at = getApp.mock.calls.length

    hidden.value = true
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(20000)
    expect(getApp.mock.calls.length).toBe(at) // 隐藏期间零请求

    hidden.value = false
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(getApp.mock.calls.length).toBe(at + 1) // 回来先补一次，不等下个周期
  })

  // 装依赖能跑一两分钟，但 90s 还停在 starting 基本就是卡住了；不给个终态用户只能对着
  // 一个永远转圈的按钮判断「到底是还在装还是死了」。
  it('starting 持续超过 90s 报一次超时，且只报一次', async () => {
    getApp.mockResolvedValue(statusOf('starting'))
    const settled = vi.fn()
    mount(Host({ onSettled: settled }))
    await vi.advanceTimersByTimeAsync(95000)
    expect(settled).toHaveBeenCalledTimes(1)
    expect(settled).toHaveBeenCalledWith('timeout')

    await vi.advanceTimersByTimeAsync(60000)
    expect(settled).toHaveBeenCalledTimes(1) // 不重复刷屏
  })

  it('回到 running 只播报一次，后续轮询不重复刷屏', async () => {
    const settled = vi.fn()
    getApp.mockResolvedValue(statusOf('running'))
    mount(Host({ onSettled: settled }))
    await vi.advanceTimersByTimeAsync(20000) // 首 tick + 4 次轮询，全是 running
    expect(settled).toHaveBeenCalledTimes(1)
    expect(settled).toHaveBeenCalledWith('running')
  })

  it('starting 中途回到 running 时超时判定作废', async () => {
    const settled = vi.fn()
    getApp.mockResolvedValueOnce(statusOf('stopped')).mockResolvedValue(statusOf('starting'))
    mount(Host({ onSettled: settled }))
    await vi.advanceTimersByTimeAsync(10000) // 进入 starting，计时器开始走
    expect(settled).not.toHaveBeenCalled()

    getApp.mockResolvedValue(statusOf('running'))
    await vi.advanceTimersByTimeAsync(2000)
    expect(settled).toHaveBeenCalledTimes(1)
    expect(settled).toHaveBeenCalledWith('running') // 不是 timeout
  })

  // 应用本来就 running 时用户点「重启预览」：状态绕一圈回到 running，不去重的话
  // 第二次就永远不报「已就绪」，按钮也会一直转圈解不开。
  it('rearm 之后再次进入同一终态要重新播报', async () => {
    const settled = vi.fn()
    let api
    getApp.mockResolvedValue(statusOf('running'))
    mount(Host({ onSettled: settled, onApi: (a) => (api = a) }))
    await vi.advanceTimersByTimeAsync(0)
    expect(settled).toHaveBeenCalledTimes(1)

    api.rearm()
    await vi.advanceTimersByTimeAsync(5000)
    expect(settled).toHaveBeenCalledTimes(2)
    expect(settled).toHaveBeenLastCalledWith('running')
  })
})
