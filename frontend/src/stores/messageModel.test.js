import { describe, it, expect } from 'vitest'
import { normalizeMessages, askQuestionsOf, askOutcomeOf, confirmStateOf, DECLINE_ANSWER } from './messageModel'
import { applyEvent } from './chat'

// 历史行的真实形状（GET /sessions/{id}/messages 直接回 DB 行）：
// 一次工具调用的 args 不在 tool 行上，而在前一条 assistant 行的
// tool_args.tool_calls[i].function.arguments，且是 JSON 字符串。
function historyRows() {
  return [
    { id: 1, seq: 1, role: 'user', content: '看下文件', created_at: '2026-10-30T00:00:00Z' },
    {
      id: 2, seq: 2, role: 'assistant', content: '让我看看',
      tool_args: {
        tool_calls: [{
          id: 'c1', type: 'function',
          function: { name: 'read_file', arguments: '{"path":"a.txt"}' },
        }],
      },
    },
    { id: 3, seq: 3, role: 'tool', tool_call_id: 'c1', tool_name: 'read_file', tool_result: { ok: true } },
    { id: 4, seq: 4, role: 'assistant', content: '好了' },
  ]
}

describe('normalizeMessages 历史行', () => {
  it('一次调用一条记录：args 从前一条 assistant 行按 tool_call_id 抽出，与 result 同体', () => {
    expect(normalizeMessages(historyRows())).toEqual([
      { role: 'user', content: '看下文件' },
      { role: 'assistant', content: '让我看看' },
      { role: 'tool', name: 'read_file', args: { path: 'a.txt' }, result: { ok: true } },
      { role: 'assistant', content: '好了' },
    ])
  })
})

// 同一次执行的实时事件流。注意：模型在工具轮里说的「让我看看」此刻还固化不下来
// （后端要等工具轮补发轮次收口，见 tickets/002），所以这里只比工具记录那一段。
function liveMessages() {
  const s = { messages: [], streamingContent: '', streamingThinking: '', toolProgress: null }
  applyEvent(s, { type: 'token', delta: '让我看看' })
  applyEvent(s, { type: 'tool_call', name: 'read_file', args: { path: 'a.txt' } })
  applyEvent(s, { type: 'tool_result', name: 'read_file', result: { ok: true } })
  return s.messages
}

describe('实时 ≡ 历史', () => {
  it('同一次执行，工具记录归一化后逐字相同', () => {
    const live = normalizeMessages(liveMessages()).filter((m) => m.role === 'tool')
    const hist = normalizeMessages(historyRows()).filter((m) => m.role === 'tool')
    expect(live).toEqual(hist)
  })
})

// 核心验收物（spec 用户故事 18）：同一次带问答的执行，实时事件流与落库后的历史行
// 归一化后必须逐条 deep-equal。这条断言锁住的是「两种来源共用一个渲染模型」，
// 底层怎么改都行，只要它还绿，刷新前后就不会叉开。
describe('实时 ≡ 历史（完整问答执行）', () => {
  const ASK_ARGS = { questions: [{ question: '选哪个', options: [{ label: 'A' }, { label: 'B' }] }] }
  const ANSWER = { answers: [{ options: ['B'] }] }

  function liveFromEvents() {
    const s = { messages: [], streamingContent: '', streamingThinking: '', toolProgress: null }
    // send() 的乐观气泡：带 client_msg_id 与空 attachments，归一化时两者都该被投影掉
    s.messages.push({ role: 'user', content: '帮我改一下', attachments: [], client_msg_id: 'm1' })
    for (const ev of [
      { type: 'token', delta: '让我先确认一件事' },
      { type: 'message_done', content: '让我先确认一件事' }, // 轮次收口排在 tool_call 之前
      { type: 'tool_call', name: 'ask_user', args: ASK_ARGS },
      { type: 'ask_user', questions: ASK_ARGS.questions }, // 前端不消费：题目由调用行承载
      { type: 'tool_result', name: 'ask_user', result: ANSWER },
      { type: 'token', delta: '好了' },
      { type: 'message_done', content: '好了' },
    ]) applyEvent(s, ev)
    return s.messages
  }

  function historyFromDb() {
    return [
      { id: 1, seq: 1, role: 'user', content: '帮我改一下', attachments: [], created_at: 't1' },
      {
        id: 2, seq: 2, role: 'assistant', content: '让我先确认一件事', reasoning: null,
        tool_args: {
          tool_calls: [{
            id: 'c1', type: 'function',
            function: { name: 'ask_user', arguments: JSON.stringify(ASK_ARGS) },
          }],
        },
      },
      { id: 3, seq: 3, role: 'tool', tool_call_id: 'c1', tool_name: 'ask_user', tool_result: ANSWER },
      { id: 4, seq: 4, role: 'assistant', content: '好了', reasoning: null },
    ]
  }

  it('归一化后逐条 deep-equal', () => {
    expect(normalizeMessages(liveFromEvents())).toEqual(normalizeMessages(historyFromDb()))
  })

  it('问答留痕在两种来源里都是同一条只读卡记录', () => {
    for (const src of [liveFromEvents(), historyFromDb()]) {
      const ask = normalizeMessages(src).find((m) => m.name === 'ask_user')
      expect(ask).toEqual({
        role: 'tool', name: 'ask_user', args: ASK_ARGS,
        questions: ASK_ARGS.questions, result: ANSWER,
      })
    }
  })
})

// 「自行决定」的固定答复即协议：模型在工具结果里读到它就停止追问。历史行里存的就是这段
// 文案，所以已答态只能靠它识别——常量必须单点定义，declineAsk 上行与识别共用同一份。
describe('askOutcomeOf 已答信息', () => {
  it('没有结果行＝待答', () => {
    expect(askOutcomeOf(null)).toEqual({ state: 'pending', answers: [], reason: '' })
    expect(askOutcomeOf(undefined).state).toBe('pending')
  })
  it('选项答案＝已答', () => {
    expect(askOutcomeOf({ answers: [{ options: ['B'] }] })).toEqual({
      state: 'answered', answers: [{ options: ['B'] }], reason: '',
    })
  })
  it('中断标记＝已中断，带原因', () => {
    expect(askOutcomeOf({ cancelled: true, reason: '提问超时' }).state).toBe('cancelled')
    expect(askOutcomeOf({ cancelled: true }).reason).toBe('')
  })
  it('「自行决定」的固定答复单独成一态，与普通手输回答区分', () => {
    expect(askOutcomeOf({ answers: [{ answer: DECLINE_ANSWER }] }).state).toBe('declined')
    expect(askOutcomeOf({ answers: [{ answer: '我自己写一句' }] }).state).toBe('answered')
  })
})

// 提问卡改由 ask_user 调用行驱动后，拿到的是**模型原始 args**（后端 _normalize_questions
// 规整过的那份只走 ask_user 事件，不落调用行）。整形只负责显示用的形状容错，
// 不做后端的校验——多题拒绝是后端的事，前端不重复一遍。
describe('askQuestionsOf 提问载荷整形', () => {
  it('新协议 questions 原样通过（选项已是对象）', () => {
    const args = { questions: [{ question: '选哪个', options: [{ label: 'A', recommended: true }], multi_select: false }] }
    expect(askQuestionsOf(args)).toEqual(args.questions)
  })
  it('字符串选项包装成 {label}', () => {
    expect(askQuestionsOf({ questions: [{ question: '选哪个', options: ['A', 'B'] }] }))
      .toEqual([{ question: '选哪个', options: [{ label: 'A' }, { label: 'B' }] }])
  })
  it('旧顶层单题参数包装成单元素列表', () => {
    expect(askQuestionsOf({ question: '选哪个', options: ['A'] }))
      .toEqual([{ question: '选哪个', options: [{ label: 'A' }] }])
  })
  it('没有题目就返回空列表（卡片不渲染，落回折叠块）', () => {
    expect(askQuestionsOf({})).toEqual([])
    expect(askQuestionsOf(null)).toEqual([])
    expect(askQuestionsOf({ questions: [] })).toEqual([])
  })
})

// 确认卡的同构：待答态靠事件打在调用行上的标记（重连时 hub 补发同一份），已答态完全靠
// 结果行里的 confirm 字段。所以结果到达后实时记录必须与历史行逐字相同——标记不能残留，
// 否则实时记录比历史多两个键（标记不落库），同构断言就此叉开。
describe('实时 ≡ 历史（带确认的写入执行）', () => {
  const WRITE_ARGS = { path: 'a.txt', content: 'hi' }
  const RESULT = { ok: true, confirm: 'approved' }

  function liveFromEvents() {
    const s = { messages: [], streamingContent: '', streamingThinking: '', toolProgress: null }
    s.messages.push({ role: 'user', content: '写个文件', attachments: [], client_msg_id: 'm1' })
    for (const ev of [
      { type: 'token', delta: '我来写' },
      { type: 'message_done', content: '我来写' },
      { type: 'tool_call', name: 'write_file', args: WRITE_ARGS },
      { type: 'confirm_request', tool: 'write_file', args: WRITE_ARGS },
      { type: 'tool_result', name: 'write_file', result: RESULT },
      { type: 'token', delta: '写好了' },
      { type: 'message_done', content: '写好了' },
    ]) applyEvent(s, ev)
    return s.messages
  }

  function historyFromDb() {
    return [
      { id: 1, seq: 1, role: 'user', content: '写个文件', attachments: [], created_at: 't1' },
      {
        id: 2, seq: 2, role: 'assistant', content: '我来写', reasoning: null,
        tool_args: {
          tool_calls: [{
            id: 'c1', type: 'function',
            function: { name: 'write_file', arguments: JSON.stringify(WRITE_ARGS) },
          }],
        },
      },
      { id: 3, seq: 3, role: 'tool', tool_call_id: 'c1', tool_name: 'write_file', tool_result: RESULT },
      { id: 4, seq: 4, role: 'assistant', content: '写好了', reasoning: null },
    ]
  }

  it('归一化后逐条 deep-equal（确认标记不残留在实时记录上）', () => {
    expect(normalizeMessages(liveFromEvents())).toEqual(normalizeMessages(historyFromDb()))
  })

  it('已答态确认卡的状态只从结果行推出，两种来源同判', () => {
    for (const src of [liveFromEvents(), historyFromDb()]) {
      const row = normalizeMessages(src).find((m) => m.name === 'write_file')
      expect(confirmStateOf(row)).toBe('approved')
    }
  })

  it('待答态只认标记：没有结果行、也没有标记的调用行不是确认卡', () => {
    const bare = normalizeMessages([{ role: 'tool', name: 'write_file', args: WRITE_ARGS }])
    expect(confirmStateOf(bare[0])).toBe(null)
    const marked = normalizeMessages([
      { role: 'tool', name: 'write_file', args: WRITE_ARGS, awaitingConfirm: true },
    ])
    expect(confirmStateOf(marked[0])).toBe('pending')
  })
})
