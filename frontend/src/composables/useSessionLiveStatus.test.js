// @vitest-environment happy-dom
// 会话状态角标的轮询节奏（spec 2026-11-04 US-9/14）。
// 直接测组合式函数：节奏与失败静默是它自己的职责，侧栏只负责把读数画成角标。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, enableAutoUnmount } from '@vue/test-utils'
import { useSessionLiveStatus } from './useSessionLiveStatus'

vi.mock('../api', () => ({ getSessionLiveStatuses: vi.fn() }))
import { getSessionLiveStatuses } from '../api'

const row = (id, extra = {}) => ({
  session_id: id,
  running: false,
  waiting: 'none',
  last_outcome: null,
  finished_at: null,
  ...extra,
})
const rows = (...list) => ({ data: { sessions: list } })

const Host = (options = {}) => ({
  setup() {
    const api = useSessionLiveStatus()
    options.onApi?.(api)
    return () => null
  },
})

// visibilitychange 监听挂在共享 document 上：不自动卸载会把多个实例一起唤醒，请求数叠成假象
enableAutoUnmount(afterEach)

beforeEach(() => {
  vi.useFakeTimers()
  getSessionLiveStatuses.mockReset().mockResolvedValue(rows())
})
afterEach(() => {
  vi.useRealTimers()
})

describe('会话状态轮询', () => {
  it('挂载即拉一次，之后每 5 秒一次', async () => {
    mount(Host())
    await vi.advanceTimersByTimeAsync(0)
    expect(getSessionLiveStatuses).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(15000)
    expect(getSessionLiveStatuses).toHaveBeenCalledTimes(4)
  })

  it('按会话 id 收成映射，供侧栏 O(1) 取角标', async () => {
    getSessionLiveStatuses.mockResolvedValue(rows(
      row(3, { running: true }),
      row(5, { waiting: 'ask', running: true }),
    ))
    let api
    mount(Host({ onApi: (a) => (api = a) }))
    await vi.advanceTimersByTimeAsync(0)
    expect(api.statuses.value).toEqual({
      3: row(3, { running: true }),
      5: row(5, { waiting: 'ask', running: true }),
    })
  })

  it('页面隐藏时不打，切回来立即补一次', async () => {
    const hidden = { value: false }
    Object.defineProperty(document, 'hidden', { get: () => hidden.value, configurable: true })

    mount(Host())
    await vi.advanceTimersByTimeAsync(5000)
    const at = getSessionLiveStatuses.mock.calls.length

    hidden.value = true
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(20000)
    expect(getSessionLiveStatuses.mock.calls.length).toBe(at) // 隐藏期间零请求

    hidden.value = false
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(getSessionLiveStatuses.mock.calls.length).toBe(at + 1) // 回来先补一次，不等下个周期
  })

  it('隐藏后紧接着切回不会叠出第二条轮询链', async () => {
    const hidden = { value: false }
    Object.defineProperty(document, 'hidden', { get: () => hidden.value, configurable: true })

    let release
    getSessionLiveStatuses.mockImplementationOnce(() => new Promise((r) => { release = r }))
    mount(Host())
    // 首次 tick 还在路上：timer 为空，切回时 onVisibility 会看到「可以续链」
    hidden.value = true
    document.dispatchEvent(new Event('visibilitychange'))
    hidden.value = false
    document.dispatchEvent(new Event('visibilitychange'))
    release(rows())
    await vi.advanceTimersByTimeAsync(0)
    expect(getSessionLiveStatuses).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(10000)
    expect(getSessionLiveStatuses).toHaveBeenCalledTimes(3) // 0s / 5s / 10s 各一次
  })

  it('请求失败静默并保留上一次读数，不把错误弹到用户脸上', async () => {
    getSessionLiveStatuses
      .mockResolvedValueOnce(rows(row(3, { running: true })))
      .mockRejectedValue(new Error('network down'))
    let api
    mount(Host({ onApi: (a) => (api = a) }))
    await vi.advanceTimersByTimeAsync(0)
    expect(api.statuses.value[3].running).toBe(true)

    await vi.advanceTimersByTimeAsync(5000)
    expect(api.statuses.value[3].running).toBe(true)
  })

  it('卸载后停止轮询', async () => {
    const w = mount(Host())
    await vi.advanceTimersByTimeAsync(0)
    const at = getSessionLiveStatuses.mock.calls.length
    w.unmount()
    await vi.advanceTimersByTimeAsync(20000)
    expect(getSessionLiveStatuses.mock.calls.length).toBe(at)
  })
})
