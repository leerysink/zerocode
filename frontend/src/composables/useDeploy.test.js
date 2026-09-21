// @vitest-environment happy-dom
// Task 9：useDeploy——/ws/deploy/{appId} 事件流订阅契约（04 §5 事件表 / T10 组件依赖）。
// 状态即数据：steps/buildTail/lastDone/prodStatus 全 ref，组件直接渲染；on() 只作副作用通道。
// FakeWebSocket 注入 globalThis.WebSocket，全程不碰真网络；重连节奏用 vitest 假定时器推进。
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { useDeploy } from './useDeploy'

vi.mock('../api', () => ({ wsUrl: (p) => 'ws://t' + p }))

class FakeWS {
  static instances = []
  constructor(url) { this.url = url; this.listeners = {}; FakeWS.instances.push(this) }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn) }
  open() { this.listeners.open?.forEach(f => f()) }
  recv(obj) { this.listeners.message?.forEach(f => f({ data: JSON.stringify(obj) })) }
  close() { this.listeners.close?.forEach(f => f()) }
}

beforeEach(() => { vi.useFakeTimers(); FakeWS.instances = []; globalThis.WebSocket = FakeWS })
afterEach(() => { vi.useRealTimers() })

describe('useDeploy', () => {
  it('connect 走 wsUrl(/ws/deploy/{id})；publish_step 累积到 steps', () => {
    const d = useDeploy(7)
    d.connect()
    const ws = FakeWS.instances.at(-1)
    expect(ws.url).toBe('ws://t/ws/deploy/7')
    ws.open()
    expect(d.connected.value).toBe(true)
    ws.recv({ type: 'publish_step', step: 'build', state: 'running' })
    expect(d.steps.value.build).toBe('running')
    ws.recv({ type: 'publish_step', step: 'build', state: 'ok' })
    expect(d.steps.value.build).toBe('ok')
    expect(d.steps.value.preflight).toBe('wait')   // 未收到事件的步骤保持初始态
  })

  it('build_log/dev_log 合并进尾部缓冲且封顶 300', () => {
    const d = useDeploy(7)
    d.connect(); FakeWS.instances.at(-1).open()
    const ws = FakeWS.instances.at(-1)
    for (let i = 0; i < 320; i++) ws.recv({ type: 'build_log', line: `l${i}` })
    expect(d.buildTail.value.length).toBe(300)
    expect(d.buildTail.value.at(-1).line).toBe('l319')
    ws.recv({ type: 'dev_log', line: 'step-x' })
    expect(d.buildTail.value.at(-1)).toMatchObject({ line: 'step-x', kind: 'step' })
    expect(d.buildTail.value.length).toBe(300)
  })

  it('prod_status 落 prodStatus；message 附进日志缓冲；publish_done 落 lastDone', () => {
    const d = useDeploy(7)
    d.connect(); const ws = FakeWS.instances.at(-1); ws.open()
    ws.recv({ type: 'prod_status', status: 'publishing' })
    expect(d.prodStatus.value).toBe('publishing')
    ws.recv({ type: 'prod_status', status: 'error', message: '起栈失败' })
    expect(d.prodStatus.value).toBe('error')
    expect(d.buildTail.value.at(-1).line).toContain('起栈失败')
    ws.recv({ type: 'publish_done', ok: true, url: 'http://x', version: 'b1', error: '' })
    expect(d.lastDone.value.url).toBe('http://x')
  })

  it('断线自动重连（open 后退避复位 1s）', () => {
    const d = useDeploy(7)
    d.connect()
    FakeWS.instances.at(-1).open()
    FakeWS.instances.at(-1).close()
    vi.advanceTimersByTime(1000)
    expect(FakeWS.instances.length).toBe(2)      // 已重连
    expect(d.connected.value).toBe(false)        // 新实例尚未 open
  })

  it('连接从未成功时退避指数增长（1s→2s），上限内恢复', () => {
    const d = useDeploy(7)
    d.connect()
    FakeWS.instances.at(-1).close()              // 第一次断：等 1s
    vi.advanceTimersByTime(999)
    expect(FakeWS.instances.length).toBe(1)
    vi.advanceTimersByTime(1)
    expect(FakeWS.instances.length).toBe(2)
    FakeWS.instances.at(-1).close()              // 第二次断（未 open 过）：等 2s
    vi.advanceTimersByTime(1999)
    expect(FakeWS.instances.length).toBe(2)
    vi.advanceTimersByTime(1)
    expect(FakeWS.instances.length).toBe(3)
  })

  it('on(publish_step) 回调与状态并存；close 后不再重连', () => {
    const d = useDeploy(7)
    const seen = []
    d.on('publish_step', (ev) => seen.push(ev.step + ':' + ev.state))
    d.connect(); FakeWS.instances.at(-1).open()
    FakeWS.instances.at(-1).recv({ type: 'publish_step', step: 'migrate', state: 'ok' })
    expect(seen).toEqual(['migrate:ok'])
    expect(d.steps.value.migrate).toBe('ok')     // 回调通道与 ref 状态不互斥
    d.close()
    FakeWS.instances.at(-1).close()
    vi.advanceTimersByTime(20000)
    expect(FakeWS.instances.length).toBe(1)      // close() 后不再重连
  })

  it('坏 JSON 与 ping 静默忽略；reset 清空 steps 与缓冲', () => {
    const d = useDeploy(7)
    d.connect(); const ws = FakeWS.instances.at(-1); ws.open()
    ws.recv({ type: 'ping' })
    ws.listeners.message.forEach(f => f({ data: '{not-json' }))
    ws.recv({ type: 'publish_step', step: 'health', state: 'running' })
    d.reset()
    expect(d.steps.value.health).toBe('wait')
    expect(d.buildTail.value).toEqual([])
    expect(d.lastDone.value).toBeNull()
  })
})
