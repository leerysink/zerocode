// 消息渲染模型的单一真源。
//
// 实时事件与历史行是两种不同形状，而且不只是字段名不同：
//   实时——tool_call 与 tool_result 是两个事件，args 在第一个事件上（reducer 已把两者折叠成一条）；
//   历史——一次调用只有一条 tool 行（tool_name/tool_result），args 不在它身上，
//         而在前一条 assistant 行的 tool_args.tool_calls[i].function.arguments，且是 JSON 字符串。
// 归一化的目标形状：一次工具调用恒为一条记录，name/args/result 同体。
// 组件只认这一种形状，不再就地兜底 `m.name ?? m.tool_name`。
//
// 归一化施加在渲染层（ChatMessages 的 computed）而非 store：store.messages 里的
// client_msg_id / pendingEcho 是 busy 回滚要用的活字段，投影掉会打断它。

// 「同名、还没结果」的那条调用记录：tool_result 到达时就地并进去，而不是另起一条。
// 找不到（历史里只有结果行、或事件乱序）时返回 null，由调用方决定补一条。
// 从后往前找即可：后端逐个派发（call→result→call→result），同一时刻同名工具至多一条待结果，
// 不存在需要在多条同名待结果记录里挑一条的情形。
export function findOpenToolCall(messages, name) {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.role === 'tool' && m.name === name && m.result === undefined) return m
  }
  return null
}

// assistant 行 tool_args.tool_calls → [{ id, name, args }]；arguments 是 JSON 字符串，解析失败按无参数处理
function callsOf(row) {
  const calls = row.tool_args?.tool_calls
  if (!Array.isArray(calls)) return []
  return calls.map((c) => {
    let args
    try {
      args = JSON.parse(c?.function?.arguments || '{}')
    } catch {
      args = undefined
    }
    return { id: c?.id, name: c?.function?.name, args }
  })
}

// 只投影渲染真正读得到的字段：id/seq/created_at/tool_call_id 一律丢掉，
// 空数组与 undefined 不写键——实时与历史由此得到逐字相同的对象形状。
function put(rec, key, value) {
  if (value === undefined || value === null) return
  if (Array.isArray(value) && value.length === 0) return
  if (value === '') return
  rec[key] = value
}

export function normalizeMessages(list) {
  const out = []
  let pending = [] // 尚未被 tool 行认领的调用参数（按 tool_call_id 配对）
  for (const raw of list || []) {
    if (!raw || typeof raw !== 'object') continue
    if (raw.role === 'tool') {
      const callId = raw.callId ?? raw.tool_call_id
      const hit = callId == null ? null : pending.find((c) => c.id === callId)
      if (hit) pending = pending.filter((c) => c !== hit)
      const rec = { role: 'tool' }
      put(rec, 'name', raw.name ?? raw.tool_name ?? hit?.name ?? 'tool')
      put(rec, 'args', raw.args ?? hit?.args)
      put(rec, 'result', raw.result ?? raw.tool_result)
      // ask_user 的题目在归一化阶段就整形好：渲染层只读字段，不再自己做形状转换
      // （否则同一个纯函数会在每次渲染里被 isAskCard 与 :questions 各调一遍）。
      if (rec.name === 'ask_user') put(rec, 'questions', askQuestionsOf(rec.args))
      put(rec, 'diff', raw.diff)
      put(rec, 'diff_path', raw.diff_path)
      put(rec, 'diff_action', raw.diff_action)
      out.push(rec)
      continue
    }
    if (raw.role === 'assistant') {
      const rec = { role: 'assistant' }
      put(rec, 'content', raw.content)
      put(rec, 'reasoning', raw.reasoning)
      out.push(rec)
      // 参数留到后续 tool 行来认领：一次调用可能有多条，按 id 配对而非按顺序
      const calls = callsOf(raw)
      if (calls.length) pending = pending.concat(calls)
      continue
    }
    const rec = { role: raw.role }
    put(rec, 'content', raw.content)
    put(rec, 'attachments', raw.attachments)
    if (raw.queued) rec.queued = true
    out.push(rec)
  }
  return out
}

// ask_user 调用行里的 args 是**模型原始参数**——后端 _normalize_questions 规整过的那份
// 只走 ask_user 事件，不落调用行。这里只做显示用的形状容错：字符串选项包成 {label}，
// 旧顶层单题参数包成单元素列表。不做后端的校验（多题拒绝是后端的职责，前端不重复一遍）。
export function askQuestionsOf(args) {
  const raw = Array.isArray(args?.questions) && args.questions.length
    ? args.questions
    : (typeof args?.question === 'string' && args.question.trim() ? [args] : [])
  return raw
    .filter((q) => q && typeof q.question === 'string' && q.question.trim())
    .map((q) => {
      const out = { question: q.question.trim() }
      if (Array.isArray(q.options)) {
        out.options = q.options.map((o) => (typeof o === 'string' ? { label: o } : o))
      }
      if (q.multi_select !== undefined) out.multi_select = !!q.multi_select
      return out
    })
}

// 结果行 → 已答信息。没有结果行就是待答；cancelled 覆盖两种情形（用户点「停止」、提问超时），
// 两者都是后端补写的配对结果行，形状一致。answers 与 questions 同序。
export function askOutcomeOf(result) {
  if (!result) return { state: 'pending', answers: [], reason: '' }
  if (result.cancelled) return { state: 'cancelled', answers: [], reason: result.reason || '' }
  return { state: 'answered', answers: Array.isArray(result.answers) ? result.answers : [], reason: '' }
}
