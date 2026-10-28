// applyEvent 纯函数测试：传入普通对象即可（不依赖 pinia/WS）
// @vitest-environment happy-dom   （Ticket 009 的 WS 接线用例需要 window/location 与 pinia）
import { describe, it, expect, vi, beforeEach, afterEach, assert } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { applyEvent, useChatStore } from './chat'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  wsUrl: (p) => 'ws://t' + p,
  getMessages: vi.fn(),
  getSessionDetail: vi.fn(),
  patchSessionModel: vi.fn(),
  uploadAttachment: vi.fn(),
  fetchAttachmentBlob: vi.fn(),
}))

function mk() {
  return {
    messages: [], streamingContent: '', streamingThinking: '', todoList: [],
    compacting: false, pendingAsk: null, currentSessionId: 1,
    pendingEcho: '', pendingEchoId: '', pendingOutbox: {}, rejectedDraft: '',
    rejectedAttachments: [], running: false, visionSupported: false,
  }
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

describe('chat store 图片发送（票 001/003/006）', () => {
  const att = { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' }

  function wsStore() {
    setActivePinia(createPinia())
    const s = useChatStore()
    s.currentSessionId = 1
    s._ws = { readyState: 1, send: vi.fn() }
    return s
  }

  it('纯图片消息允许发送，上行带 attachment_ids 与 client_msg_id', () => {
    const s = wsStore()
    s.visionSupported = true
    assert.equal(s.send({ text: '', attachments: [att] }), true)
    assert.equal(s.messages[0].content, '')
    assert.deepEqual(s.messages[0].attachments, [att])
    const payload = JSON.parse(s._ws.send.mock.calls[0][0])
    assert.equal(payload.type, 'user_message')
    assert.deepEqual(payload.attachment_ids, [1])
    assert.ok(payload.client_msg_id)
  })

  it('非视觉模型带附件本地拦截，不产生气泡与 outbox', () => {
    const s = wsStore()
    s.visionSupported = false
    assert.equal(s.send({ text: '看图', attachments: [att] }), false)
    assert.equal(s.messages.length, 0)
    assert.match(s.hint, /不支持图片/)
    assert.deepEqual(Object.keys(s.pendingOutbox), [])
  })

  it('busy 带 client_msg_id 时回滚对应气泡并恢复文字与附件', () => {
    const s = wsStore()
    s.visionSupported = true
    s.send({ text: '看图', attachments: [att] })
    const cid = s.messages[0].client_msg_id
    applyEvent(s, { type: 'busy', message: '不行', client_msg_id: cid, running: false })
    assert.equal(s.messages.length, 0)
    assert.equal(s.rejectedDraft, '看图')
    assert.deepEqual(s.rejectedAttachments, [att])
    assert.equal(s.running, false)
  })

  it('切走会话后 busy 只清 outbox，不把旧草稿回填到新会话', () => {
    const s = wsStore()
    s.visionSupported = true
    s.send({ text: '看图', attachments: [att] })
    const cid = s.messages[0].client_msg_id
    s.currentSessionId = 2
    applyEvent(s, { type: 'busy', message: '不行', client_msg_id: cid, running: false })
    assert.equal(s.rejectedDraft, '')
    assert.deepEqual(s.rejectedAttachments, [])
    assert.deepEqual(s.pendingOutbox, {})
  })

  it('steering_done 按 client_msg_id 只摘对应气泡的角标', () => {
    const s = wsStore()
    s.running = true
    s.visionSupported = true
    s.send({ text: '一', attachments: [att] })
    s.send({ text: '二', attachments: [att] })
    const first = s.messages[0].client_msg_id
    applyEvent(s, { type: 'steering_done', content: '一', client_msg_id: first })
    assert.equal(s.messages[0].queued, false)
    assert.equal(s.messages[1].queued, true)
  })

  it('cancel 把排队气泡的文字与附件一起退回输入框', () => {
    const s = wsStore()
    s.running = true
    s.visionSupported = true
    s.send({ text: '一', attachments: [att] })
    s.send({ text: '二', attachments: [att] })
    s.cancel()
    assert.equal(s.rejectedDraft, '一\n\n二')
    assert.equal(s.rejectedAttachments.length, 2)
    assert.equal(s.messages.filter((m) => m.role === 'user').length, 0)
  })

  it('retry 重新下载并上传图片，再用新附件 ID 发送', async () => {
    const s = wsStore()
    s.visionSupported = true
    const old = { ...att, id: 9, url: '/api/attachments/9' }
    s.messages.push({ role: 'user', content: '看图', attachments: [old] })
    const { fetchAttachmentBlob, uploadAttachment } = await import('../api')
    fetchAttachmentBlob.mockResolvedValue({ data: new Blob(['x'], { type: 'image/png' }) })
    uploadAttachment.mockResolvedValue({
      data: { id: 10, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/10' },
    })
    const spy = vi.spyOn(s, 'send').mockReturnValue(true)
    await s.retry()
    expect(fetchAttachmentBlob).toHaveBeenCalledWith('/api/attachments/9')
    expect(uploadAttachment).toHaveBeenCalled()
    expect(spy).toHaveBeenCalledWith({
      text: '看图',
      attachments: [{ id: 10, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/10' }],
    })
  })

  it('openSession 写入 effective_model 并还原对象形态的 pending_steering', async () => {
    const { getMessages, getSessionDetail } = await import('../api')
    getMessages.mockResolvedValue({ data: [] })
    getSessionDetail.mockResolvedValue({ data: {
      todo_state: [], model_config_id: null,
      effective_model: { id: null, name: 'deepseek-chat', model: 'deepseek-chat', supports_vision: false, source: 'global' },
      pending_steering: [{ content: '看图', client_msg_id: 'c1', attachments: [att] }],
    } })
    setActivePinia(createPinia())
    const s = useChatStore()
    s.connect = vi.fn()
    await s.openSession(5, 2)
    expect(s.effectiveModel.model).toBe('deepseek-chat')
    expect(s.visionSupported).toBe(false)
    expect(s.messages[0]).toMatchObject({ role: 'user', content: '看图', queued: true, attachments: [att] })
    expect(s.messages[0].client_msg_id).toBe('c1')
  })
})
