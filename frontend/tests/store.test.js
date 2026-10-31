import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore, applyEvent } from '../src/stores/chat'
import { DECLINE_ANSWER } from '../src/stores/messageModel'
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
  it('一次工具调用折叠成一条记录：args 与 result 同体', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'tool_result', name: 'write_file', result: { ok: true } })
    const tools = s.messages.filter(m => m.role === 'tool')
    expect(tools).toHaveLength(1)
    expect(tools[0]).toMatchObject({ name: 'write_file', args: { path: 'a.txt' }, result: { ok: true } })
  })
  it('ask_user 只在没有待答调用行时合成一条，confirm_request 只给调用行打标记', () => {
    const s = useChatStore()
    // 实时流里 tool_call 已先行建好行，事件不得再补一条（否则一次提问画两张卡）
    applyEvent(s, { type: 'tool_call', name: 'ask_user', args: { questions: [{ question: '选哪个' }] } })
    applyEvent(s, { type: 'ask_user', questions: [{ question: '选哪个', options: [{ label: 'A' }] }] })
    expect(s.messages.filter(m => m.name === 'ask_user')).toHaveLength(1)
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'confirm_request', tool: 'write_file', args: { path: 'a.txt' } })
    expect(s.messages.at(-1).awaitingConfirm).toBe(true)
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
  it('error 复位 compacting（压缩失败也是终态，压缩按钮不得永久 loading）', () => {
    const s = useChatStore()
    s.compacting = true
    applyEvent(s, { type: 'error', message: '压缩失败，已保留原文' })
    expect(s.compacting).toBe(false)
  })
  it('notice 终态提示（压缩 skip：消息不足）：复位 compacting 并渲染提示条', () => {
    const s = useChatStore()
    s.compacting = true
    applyEvent(s, { type: 'notice', text: '历史消息不足，暂无需压缩' })
    expect(s.compacting).toBe(false)
    expect(s.messages.at(-1)).toMatchObject({ role: 'notice', content: '历史消息不足，暂无需压缩' })
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
  it('上行失败（WS 非 OPEN）不发帧；两张卡都由消息流驱动，不随上行成败增删', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'ask_user', args: { questions: [{ question: '选哪个', options: [{ label: 'A' }] }] } })
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'confirm_request', tool: 'write_file', args: { path: 'a.txt' } })
    const send = vi.fn()
    vi.spyOn(console, 'warn').mockImplementation(() => {}) // 静音 _uplink 的未就绪告警
    // 模拟断线→重连窗口期：readyState=0（CONNECTING），上行失败不发帧，卡片留在消息流里供重试
    s._ws = { readyState: 0, send }
    s.submitAsk([{ options: ['A'] }])
    s.confirm(true)
    expect(send).not.toHaveBeenCalled()
    expect(s.messages.filter(m => m.role === 'tool')).toHaveLength(2)
    expect(s.messages.at(-1).awaitingConfirm).toBe(true)
    // 连接就绪（readyState=1 即 OPEN）后重试同一操作：真正发出；两张卡都仍要等结果行才翻转
    s._ws = { readyState: 1, send }
    s.submitAsk([{ options: ['A'] }])
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: 'ask_submit', answers: [{ options: ['A'] }] }))
    s.confirm(true)
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: 'confirm', approved: true }))
    expect(s.messages.filter(m => m.role === 'tool')).toHaveLength(2)
    expect(s.messages.at(-1).awaitingConfirm).toBe(true)
  })
  it('declineAsk 上行「自行决定」固定答复，卡片留在消息流等结果行翻转（不是取消执行）', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'ask_user', args: { question: '选哪个' } })
    const send = vi.fn()
    s._ws = { readyState: 1, send }
    s.declineAsk()
    const payload = JSON.parse(send.mock.calls[0][0])
    expect(payload.type).toBe('ask_submit')
    expect(payload.answers).toHaveLength(1)
    expect(payload.answers[0].answer).toContain('自行决定')
    expect(payload.answers[0].answer).toContain('不要再提问')
    // 文案单点定义：上行与已答态识别共用同一份常量，两处各写一遍必然漂移
    expect(payload.answers[0].answer).toBe(DECLINE_ANSWER)
    // 卡片由结果行翻转，本地不清任何字段：上行后待答卡仍在消息流里
    expect(s.messages.filter(m => m.role === 'tool' && !m.result)).toHaveLength(1)
  })
  it('重连补发的 confirm_request 能把历史里的调用行重新标成待答（用户故事 16）', async () => {
    stubWsGlobals()
    // 刷新后拿到的历史：写入调用行已落库，结果行还没有——但光看行分不清
    // 「在等确认」还是「工具正在跑」，这个事实只存在于 confirm_request 事件里
    getMessages.mockResolvedValue({ data: [
      { id: 1, seq: 1, role: 'user', content: '写个文件', attachments: [] },
      { id: 2, seq: 2, role: 'assistant', content: '我来写',
        tool_args: { tool_calls: [{ id: 'c1', type: 'function',
          function: { name: 'write_file', arguments: '{"path":"a.txt"}' } }] } },
      { id: 3, seq: 3, role: 'tool', tool_call_id: 'c1', tool_name: 'write_file' },
    ] })
    const s = useChatStore()
    await s.openSession('s1')
    expect(s.messages.filter(m => m.role === 'tool' && !m.result)).toHaveLength(1)
    // hub 在连入时补发同一份确认请求 → 标记打回那一行，卡片恢复可交互
    applyEvent(s, { type: 'confirm_request', tool: 'write_file', args: { path: 'a.txt' } })
    const marked = s.messages.filter(m => m.awaitingConfirm)
    expect(marked).toHaveLength(1)
    expect(marked[0].tool_name).toBe('write_file')
  })
  it('刷新后回答提问：结果折叠进历史行，而不是另起一条', async () => {
    stubWsGlobals()
    getMessages.mockResolvedValue({ data: [
      { id: 2, seq: 2, role: 'assistant', content: '先确认一件事',
        tool_args: { tool_calls: [{ id: 'c1', type: 'function',
          function: { name: 'ask_user', arguments: '{"question":"选哪个"}' } }] } },
      { id: 3, seq: 3, role: 'tool', tool_call_id: 'c1', tool_name: 'ask_user' },
    ] })
    const s = useChatStore()
    await s.openSession('s1')
    applyEvent(s, { type: 'tool_result', name: 'ask_user', result: { answers: [{ options: ['A'] }] } })
    const tools = s.messages.filter(m => m.role === 'tool')
    expect(tools).toHaveLength(1)
    expect(tools[0].result).toEqual({ answers: [{ options: ['A'] }] })
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

  it('openSession 详情拉取失败降级：不阻断打开（todo/模型置空，照常渲染历史并连接）', async () => {
    stubWsGlobals()
    getMessages.mockResolvedValue({ data: [{ role: 'user', content: '历史' }] })
    getSessionDetail.mockRejectedValueOnce(new Error('detail boom'))
    const s = useChatStore()
    await expect(s.openSession('s1', 'w1')).resolves.toBeUndefined() // detail 失败不炸 openSession
    expect(s.todoList).toEqual([])
    expect(s.currentModelId).toBeNull()
    expect(s.messages).toEqual([{ role: 'user', content: '历史' }]) // 历史照常渲染
    expect(s._ws).toBeTruthy() // connect 照常执行
  })

  it('send/cancel 复位 streamingThinking（与 streamingContent 对齐）', () => {
    const s = useChatStore()
    s._ws = { readyState: 1, send: vi.fn() }
    s.streamingThinking = '残留'
    s.send('hi')
    expect(s.streamingThinking).toBe('')
    s.streamingThinking = '残留'
    s.cancel()
    expect(s.streamingThinking).toBe('')
  })

  // ---- 死循环修复：执行中不得把 UI 打进「发不出也没停止按钮」的状态 ----

  it('busy 是非终态提示：不清 running，回滚乐观 user 气泡并把文本交回草稿', () => {
    const s = useChatStore()
    const send = vi.fn()
    s._ws = { readyState: 1, send }
    s.send('结束语') // 前端本地没在跑（脱钩场景），正常上行并落乐观气泡
    expect(send).toHaveBeenCalledTimes(1)
    expect(s.messages.at(-1)).toMatchObject({ role: 'user', content: '结束语' })
    s.running = true // 后端仍在跑（重连快照已把执行态立回来）
    applyEvent(s, { type: 'busy', message: '会话正在执行中' })
    expect(s.running).toBe(true) // 修复前收到的是 error → running 被清 → 「停止」按钮消失
    expect(s.messages.some((m) => m.role === 'user')).toBe(false) // 后端没落库的幻影气泡回滚
    expect(s.rejectedDraft).toBe('结束语') // 文本交回输入框，不让用户重打
    expect(s.hint).toBe('会话正在执行中')
  })

  it('status(running) 快照置回执行态；status(idle) 仍是终态复位', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'status', status: 'running' }) // 刷新/重连时后端补发的在跑快照
    expect(s.running).toBe(true)
    applyEvent(s, { type: 'status', status: 'idle' })
    expect(s.running).toBe(false)
  })

  it('刷新重开会话：执行态不靠 DB status 猜，由后端 status(running) 快照立回来', async () => {
    stubWsGlobals()
    getMessages.mockResolvedValue({ data: [{ role: 'user', content: '历史' }] })
    // DB 里 status='running' 可能是崩溃残留（watchdog 最长 10 分钟才收），拿它立执行态
    // 会造出「假在跑、停不掉」的新死结：openSession 一律先置空闲
    getSessionDetail.mockResolvedValue({
      data: { todo_state: [], model_config_id: null, status: 'running' },
    })
    const s = useChatStore()
    await s.openSession('s1', 'w1')
    expect(s.running).toBe(false)
    // 真在跑的话，connect 上后端补发一帧快照，「停止」按钮随即可见
    applyEvent(s, { type: 'status', status: 'running' })
    expect(s.running).toBe(true)
  })

  it('压缩中 send 本地拦截：不上行、不落 user 气泡、保留草稿并提示', () => {
    const s = useChatStore()
    const send = vi.fn()
    s._ws = { readyState: 1, send }
    s.compacting = true
    expect(s.send('再试')).toBe(false)
    expect(send).not.toHaveBeenCalled()
    expect(s.messages.some((m) => m.role === 'user')).toBe(false)
    expect(s.rejectedDraft).toBe('再试')
    expect(s.hint).toContain('压缩')
    // 压缩结束后重发同一句：上行成功、落气泡、上一条提示与草稿一并清掉（不留残留）
    s.compacting = false
    expect(s.send('再试')).toBe(true)
    expect(s.hint).toBe('')
    expect(s.rejectedDraft).toBe('')
    expect(s.messages.at(-1)).toMatchObject({ role: 'user', content: '再试' })
    // 上行协议已带附件占位与 client_msg_id（图片输入任务）：只比对业务字段
    const out = JSON.parse(send.mock.calls.at(-1)[0])
    expect(out).toMatchObject({ type: 'user_message', content: '再试', attachment_ids: [] })
    expect(out.client_msg_id).toBeTruthy()
  })

  it('连接未就绪时 send：先落的气泡与执行态一并回滚，文本进草稿并提示重连', () => {
    const s = useChatStore()
    const send = vi.fn()
    vi.spyOn(console, 'warn').mockImplementation(() => {}) // 静音 _uplink 的未就绪告警
    s._ws = { readyState: 0, send } // 断线→重连窗口期（指数退避中）
    expect(s.send('还没连上')).toBe(false)
    expect(send).not.toHaveBeenCalled()
    expect(s.messages.some((m) => m.role === 'user')).toBe(false) // 后端没收到，不留假气泡
    expect(s.running).toBe(false)
    expect(s.rejectedDraft).toBe('还没连上')
    expect(s.hint).toContain('重连')
  })

  it('执行中再发消息 = 插话：照常上行并落「排队中」气泡，不再本地拒发', () => {
    const s = useChatStore()
    const send = vi.fn()
    s._ws = { readyState: 1, send }
    s.running = true
    expect(s.send('改成用 SQLite')).toBe(true)
    const out = JSON.parse(send.mock.calls.at(-1)[0])
    expect(out).toMatchObject({ type: 'user_message', content: '改成用 SQLite', attachment_ids: [] })
    expect(out.client_msg_id).toBeTruthy()
    expect(s.messages.at(-1)).toMatchObject({ role: 'user', content: '改成用 SQLite', queued: true })
    expect(s.running).toBe(true) // 插话不另起一轮，执行态当然保持
    expect(s.hint).toBe('')
    // 注入完成：角标摘掉，气泡原地转正（不重复追加）
    applyEvent(s, { type: 'steering_done', content: '改成用 SQLite' })
    expect(s.messages.filter((m) => m.role === 'user')).toHaveLength(1)
    expect(s.messages.at(-1).queued).toBeFalsy()
  })

  it('停止：未送达的插话从消息流摘掉，文本按原顺序退回输入框并说明条数', () => {
    const s = useChatStore()
    s._ws = { readyState: 1, send: vi.fn() }
    s.running = true
    s.send('第一句插话')
    s.send('第二句插话')
    applyEvent(s, { type: 'steering_done', content: '第一句插话' }) // 只有第二句还没送达
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    s.cancel()
    expect(s.messages.some((m) => m.role === 'user' && m.queued)).toBe(false)
    expect(s.messages.some((m) => m.content === '第二句插话')).toBe(false)
    expect(s.rejectedDraft).toBe('第二句插话')
    expect(s.hint).toContain('1 条插话未送达')
    expect(s.running).toBe(false)
  })

  it('openSession 还原「排队中」的插话气泡：刷新不丢刚说出去还没被读到的话', async () => {
    stubWsGlobals()
    getMessages.mockResolvedValue({ data: [{ role: 'user', content: '原问题' }] })
    getSessionDetail.mockResolvedValue({
      data: { todo_state: [], model_config_id: null, status: 'running', pending_steering: ['补一句A', '补一句B'] },
    })
    const s = useChatStore()
    await s.openSession('s1', 'w1')
    expect(s.messages).toEqual([
      { role: 'user', content: '原问题' },
      { role: 'user', content: '补一句A', queued: true },
      { role: 'user', content: '补一句B', queued: true },
    ])
  })

  it('setModel 回写会话校验：await 期间已切换会话则不得陈旧回写', async () => {
    const s = useChatStore()
    let resolvePatch
    patchSessionModel.mockImplementation(() => new Promise((r) => { resolvePatch = r }))
    s.currentSessionId = 's1'
    s.currentModelId = 3
    const pending = s.setModel(7)
    s.currentSessionId = 's2' // await 期间用户切到 s2
    resolvePatch({ data: {} }) // axios 响应恒为对象；此处只需满足 setModel 的取数形态
    await pending
    expect(s.currentModelId).toBe(3) // s1 的模型不得写回 s2 的视图
  })
})
