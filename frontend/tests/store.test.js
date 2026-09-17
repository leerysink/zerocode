import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore, applyEvent } from '../src/stores/chat'
import { getMessages, getSessionDetail, listModels, patchSessionModel } from '../src/api'

// 把 openSession/模型 actions 依赖的 REST 换成可编排的 mock（openSession 依赖
// getMessages/getSessionDetail，loadModels/setModel 依赖 listModels/patchSessionModel）；
// 其余导出保持真实实现，既有 connect 用例仍走真实 wsUrl（配合 window stub）。
vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    getMessages: vi.fn(),
    getSessionDetail: vi.fn(),
    listModels: vi.fn(),
    patchSessionModel: vi.fn(),
  }
})

describe('chat store 事件流', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    // openSession 详情拉取默认成功（单测按需覆盖返回值）
    getSessionDetail.mockResolvedValue({ data: { todo_state: [], model_config_id: null } })
  })
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
  it('tool_call/tool_result 成对追加，ask_user（多问题新协议）/confirm 置 pending', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'tool_result', name: 'write_file', result: { ok: true } })
    expect(s.messages.filter(m => m.role === 'tool')).toHaveLength(2)
    applyEvent(s, { type: 'ask_user', questions: [{ question: '选哪个', options: [{ label: 'A' }] }] })
    expect(s.pendingAsk).toEqual({ questions: [{ question: '选哪个', options: [{ label: 'A' }] }] })
    applyEvent(s, { type: 'confirm_request', tool: 'write_file' })
    expect(s.pendingConfirm).toBeTruthy()
  })
  it('error 追加错误消息并复位 running/thinking', () => {
    const s = useChatStore()
    s.running = true
    s.streamingThinking = '想'
    applyEvent(s, { type: 'error', message: 'boom' })
    expect(s.messages.at(-1)).toMatchObject({ role: 'error', content: 'boom' })
    expect(s.running).toBe(false)
    expect(s.streamingThinking).toBe('')
  })
  it('file_change 事件挂到最近 tool 消息，且保留 path/action 供 diff 渲染文件名', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'file_change', path: 'a.txt', action: 'create', diff: '--- a/a.txt' })
    const tool = s.messages.filter(m => m.role === 'tool').at(-1)
    expect(tool.diff).toBe('--- a/a.txt')
    expect(tool.diff_path).toBe('a.txt')
    expect(tool.diff_action).toBe('create')
  })
  it('上行失败（WS 非 OPEN）保留 pendingAsk/pendingConfirm，成功才清除', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'ask_user', questions: [{ question: '选哪个', options: [{ label: 'A' }] }] })
    applyEvent(s, { type: 'confirm_request', tool: 'write_file' })
    const send = vi.fn()
    vi.spyOn(console, 'warn').mockImplementation(() => {}) // 静音 _uplink 的未就绪告警
    // 模拟断线→重连窗口期：readyState=0（CONNECTING），上行失败须保留卡片供稍后重试
    s._ws = { readyState: 0, send }
    s.submitAsk([{ options: ['A'] }])
    s.confirm(true)
    expect(s.pendingAsk).toEqual({ questions: [{ question: '选哪个', options: [{ label: 'A' }] }] })
    expect(s.pendingConfirm).toBeTruthy()
    expect(send).not.toHaveBeenCalled()
    // 连接就绪（readyState=1 即 OPEN）后重试同一操作：真正发出并清除卡片
    s._ws = { readyState: 1, send }
    s.submitAsk([{ options: ['A'] }])
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: 'ask_submit', answers: [{ options: ['A'] }] }))
    expect(s.pendingAsk).toBeNull()
    s.confirm(true)
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: 'confirm', approved: true }))
    expect(s.pendingConfirm).toBeNull()
  })
  it('requestCompact 上行 compact；loadModels/setModel 走 REST 并同步 currentModelId', async () => {
    const s = useChatStore()
    const send = vi.fn()
    s._ws = { readyState: 1, send }
    s.requestCompact()
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: 'compact' }))
    listModels.mockResolvedValue({ data: [{ id: 1, name: 'm1' }] })
    await s.loadModels()
    expect(s.models).toEqual([{ id: 1, name: 'm1' }])
    patchSessionModel.mockResolvedValue({ data: {} })
    s.currentSessionId = 's1'
    await s.setModel(7)
    expect(patchSessionModel).toHaveBeenCalledWith('s1', 7)
    expect(s.currentModelId).toBe(7)
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

  // openSession 走 connect→wsUrl 需要 window/WebSocket 全局（仅新加的两个用例需要）
  function stubWsGlobals() {
    vi.stubGlobal('window', { location: { protocol: 'http:', host: 'test' } })
    vi.stubGlobal('WebSocket', class {
      constructor() { this.readyState = 0 }
      close() { this.readyState = 3 }
    })
  }

  it('openSession 记录 currentWorkspaceId 与详情 todo/模型；不传 workspaceId 时不清空当前值', async () => {
    stubWsGlobals()
    getMessages.mockResolvedValue({ data: [] })
    getSessionDetail.mockResolvedValue({
      data: { todo_state: [{ content: 'A', status: 'pending' }], model_config_id: 3 },
    })
    const s = useChatStore()
    await s.openSession('s1', 'w1')
    expect(s.currentWorkspaceId).toBe('w1')
    expect(s.todoList).toEqual([{ content: 'A', status: 'pending' }])
    expect(s.currentModelId).toBe(3)
    await s.openSession('s2') // 参数缺省兜底（Task 13/14 以 currentWorkspaceId 为渲染门，不得被清）
    expect(s.currentWorkspaceId).toBe('w1')
    expect(s.currentSessionId).toBe('s2')
  })

  it('openSession 快速切换：后到的慢响应被守卫丢弃，不得覆盖新会话的 messages', async () => {
    stubWsGlobals()
    const s = useChatStore()
    let resolveA
    const slowA = new Promise((resolve) => { resolveA = resolve })
    getMessages.mockImplementation((id) =>
      id === 'sA' ? slowA : Promise.resolve({ data: [{ role: 'user', content: 'B 的消息' }] })
    )
    const first = s.openSession('sA', 'wA') // A 慢，挂起
    await s.openSession('sB', 'wB') // B 快，先完成
    expect(s.currentSessionId).toBe('sB')
    expect(s.messages).toEqual([{ role: 'user', content: 'B 的消息' }])
    resolveA({ data: [{ role: 'user', content: 'A 的消息' }] }) // A 的响应后到
    await first
    expect(s.messages).toEqual([{ role: 'user', content: 'B 的消息' }]) // 守卫：A 不覆盖 B
    expect(s.currentWorkspaceId).toBe('wB')
  })
})
