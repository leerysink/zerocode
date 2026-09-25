// applyEvent 纯函数测试：传入普通对象即可（不依赖 pinia/WS）
// @vitest-environment happy-dom   （Ticket 009 的 WS 接线用例需要 window/location 与 pinia）
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { applyEvent, useChatStore } from './chat'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  wsUrl: (p) => 'ws://t' + p,
}))

function mk() {
  return { messages: [], streamingContent: '', streamingThinking: '', todoList: [], compacting: false, pendingAsk: null }
}

describe('applyEvent 新事件', () => {
  it('reasoning_delta 累积', () => {
    const s = mk()
    applyEvent(s, { type: 'reasoning_delta', content: '想' })
    applyEvent(s, { type: 'reasoning_delta', content: '清楚' })
    expect(s.streamingThinking).toBe('想清楚')
  })
  it('message_done 附带 reasoning 并复位', () => {
    const s = mk()
    s.streamingThinking = '想'
    applyEvent(s, { type: 'message_done', content: '答' })
    expect(s.messages[0]).toEqual({ role: 'assistant', content: '答', reasoning: '想' })
    expect(s.streamingThinking).toBe('')
  })
  it('todo_sync 覆盖清单', () => {
    const s = mk()
    const todos = [{ content: 'A', status: 'pending' }]
    applyEvent(s, { type: 'todo_sync', todos })
    expect(s.todoList).toEqual(todos)
  })
  it('compacting/compaction_done 状态与提示条', () => {
    const s = mk()
    applyEvent(s, { type: 'compacting' })
    expect(s.compacting).toBe(true)
    applyEvent(s, { type: 'compaction_done', summary: 'x', up_to_seq: 4 })
    expect(s.compacting).toBe(false)
    expect(s.messages[0].role).toBe('notice')
  })
  it('error/status 复位 thinking', () => {
    const s = mk()
    s.streamingThinking = 'x'
    applyEvent(s, { type: 'status', status: 'idle' })
    expect(s.streamingThinking).toBe('')
  })
  it('session_title 就地更新侧栏列表中当前会话的标题（不等重新拉取）', () => {
    const s = {
      ...mk(),
      currentSessionId: 5,
      currentWorkspaceId: 2,
      sessionsByWs: { 2: [{ id: 5, title: '新会话' }, { id: 6, title: '别的会话' }] },
    }
    applyEvent(s, { type: 'session_title', title: '待办清单应用' })
    expect(s.sessionsByWs[2][0].title).toBe('待办清单应用')
    expect(s.sessionsByWs[2][1].title).toBe('别的会话') // 只动当前会话
  })
  it('session_title 找不到对应条目时安全忽略（未加载的分组/已删会话）', () => {
    const s = { ...mk(), currentSessionId: 99, currentWorkspaceId: 2, sessionsByWs: {} }
    expect(() => applyEvent(s, { type: 'session_title', title: 'x' })).not.toThrow()
  })
})

// Ticket 009：WS 认证收口的前端侧——close code 4401（未登录）跳登录页、4404（会话不可用）
// 不再重连；两者都不能落进既有指数退避重连循环（对认证拒绝无限重连只会刷屏）。
describe('WS onclose 认证分支（Ticket 009）', () => {
  class PropWS {
    static instances = []
    constructor(url) { this.url = url; this.readyState = 1; PropWS.instances.push(this) }
    send() {}
    close() {}
  }
  let savedLocation
  beforeEach(() => {
    setActivePinia(createPinia())
    PropWS.instances = []
    globalThis.WebSocket = PropWS
    vi.useFakeTimers()
    savedLocation = window.location
  })
  afterEach(() => { vi.useRealTimers(); Object.defineProperty(window, 'location', { value: savedLocation, writable: true, configurable: true }) })

  function stubLocation() {
    const assign = vi.fn()
    Object.defineProperty(window, 'location', { value: { assign }, writable: true, configurable: true })
    return assign
  }

  it('4401：跳登录页且不再重连', () => {
    const assign = stubLocation()
    const store = useChatStore()
    store.connect(5)
    expect(PropWS.instances.length).toBe(1)
    PropWS.instances.at(-1).onclose({ code: 4401 })
    vi.advanceTimersByTime(60000)
    expect(PropWS.instances.length).toBe(1)
    expect(assign).toHaveBeenCalledWith('/login')
  })

  it('4404：会话不可用（被删/越权），停止重连也不跳登录', () => {
    const assign = stubLocation()
    const store = useChatStore()
    store.connect(5)
    PropWS.instances.at(-1).onclose({ code: 4404 })
    vi.advanceTimersByTime(60000)
    expect(PropWS.instances.length).toBe(1)
    expect(assign).not.toHaveBeenCalled()
    expect(store.wsStatus).toBe('disconnected')
  })

  it('普通断线（无 44xx）仍按退避重连（既有行为不回退）', () => {
    const assign = stubLocation()
    const store = useChatStore()
    store.connect(5)
    PropWS.instances.at(-1).onclose({ code: 1006 })
    vi.advanceTimersByTime(60000)
    expect(PropWS.instances.length).toBeGreaterThan(1)
    expect(assign).not.toHaveBeenCalled()
  })
})
