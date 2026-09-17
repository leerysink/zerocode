import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore, applyEvent } from '../src/stores/chat'

describe('chat store 事件流', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })
  it('token 追加 streamingContent，message_done 固化', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'token', delta: '你' })
    applyEvent(s, { type: 'token', delta: '好' })
    expect(s.streamingContent).toBe('你好')
    applyEvent(s, { type: 'message_done', content: '你好' })
    expect(s.streamingContent).toBe('')
    expect(s.messages.at(-1)).toMatchObject({ role: 'assistant', content: '你好' })
  })
  it('tool_call/tool_result 成对追加，ask_user/confirm 置 pending', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'tool_result', name: 'write_file', result: { ok: true } })
    expect(s.messages.filter(m => m.role === 'tool')).toHaveLength(2)
    applyEvent(s, { type: 'ask_user', question: '选哪个', options: ['A'] })
    expect(s.pendingAsk).toMatchObject({ question: '选哪个' })
    applyEvent(s, { type: 'confirm_request', tool: 'write_file' })
    expect(s.pendingConfirm).toBeTruthy()
  })
  it('error 追加错误消息并复位 running', () => {
    const s = useChatStore()
    s.running = true
    applyEvent(s, { type: 'error', message: 'boom' })
    expect(s.messages.at(-1)).toMatchObject({ role: 'error', content: 'boom' })
    expect(s.running).toBe(false)
  })
  it('file_change 事件挂到最近 tool 消息', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'file_change', path: 'a.txt', action: 'create', diff: '--- a/a.txt' })
    const tool = s.messages.filter(m => m.role === 'tool').at(-1)
    expect(tool.diff).toBe('--- a/a.txt')
  })
  it('上行失败（WS 非 OPEN）保留 pendingAsk/pendingConfirm，成功才清除', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'ask_user', question: '选哪个', options: ['A'] })
    applyEvent(s, { type: 'confirm_request', tool: 'write_file' })
    const send = vi.fn()
    vi.spyOn(console, 'warn').mockImplementation(() => {}) // 静音 _uplink 的未就绪告警
    // 模拟断线→重连窗口期：readyState=0（CONNECTING），上行失败须保留卡片供稍后重试
    s._ws = { readyState: 0, send }
    s.answerAsk('B')
    s.chooseAsk('A')
    s.confirm(true)
    expect(s.pendingAsk).toEqual({ question: '选哪个', options: ['A'] })
    expect(s.pendingConfirm).toBeTruthy()
    expect(send).not.toHaveBeenCalled()
    // 连接就绪（readyState=1 即 OPEN）后重试同一操作：真正发出并清除卡片
    s._ws = { readyState: 1, send }
    s.chooseAsk('A')
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: 'ask_choice', option: 'A' }))
    expect(s.pendingAsk).toBeNull()
    s.confirm(true)
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: 'confirm', approved: true }))
    expect(s.pendingConfirm).toBeNull()
  })
  it('connect 入口重置退避计数；定时重连沿用同一断线期的指数退避', () => {
    vi.stubGlobal('window', { location: { protocol: 'http:', host: 'test' } })
    vi.stubGlobal('WebSocket', class {
      constructor() { this.readyState = 0 }
      close() { this.readyState = 3 }
    })
    vi.useFakeTimers()
    const s = useChatStore()
    s._retry = 4 // 模拟上一会话残留的退避计数
    s.connect('s1')
    expect(s._retry).toBe(0) // 入口重置：用户发起的新连接不沿用旧退避
    s._ws.onopen()
    expect(s.wsStatus).toBe('connected')
    s._ws.onclose() // 首次断开：1s 后重连，计数 +1
    vi.advanceTimersByTime(1000)
    expect(s._retry).toBe(1) // 定时重连不清计数
    s._ws.onclose() // 再次断开：退避翻倍为 2s
    vi.advanceTimersByTime(1000)
    expect(s._retry).toBe(2) // 2s 未到，尚未重连
    vi.advanceTimersByTime(1000)
    expect(s._retry).toBe(2) // 已重连（keepBackoff 路径，入口未重置）
  })
})
