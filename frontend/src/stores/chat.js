// chat store：会话/消息/待办/模型/WS 状态。事件处理抽为导出的纯函数 applyEvent(store, ev)，
// 便于 vitest 不经组件直接调用（connect 的 onmessage 只做 applyEvent(this, JSON.parse(e.data))）。
// 下行事件类型与后端 loop.py / hub.py 协议一致：
// token / reasoning_delta / tool_call / tool_result / confirm_request / message_done
// / error / status / file_change / todo_sync / compacting / compaction_done / notice(压缩 skip 终态)
// / busy(执行中被拒的非终态提示) / steering_done(插话已在工具边界被模型看见)
// / tool_progress(预览工具的临时进度，不落库不进气泡，收到结果/终态即清)
// / subrun_start/text/tool_call/tool_result/progress/done(委派卡的子 run 事件，按 index
//   归并进 delegate 调用记录；没有 done 的行在委派结果 cancelled 时收口成已中断)
// / subrun_snapshot(批量委派执行中连入的补发：合成/合并委派卡的当前批次状态，步骤从历史行还原)
// / session_title(首条消息自动命名，就地更新侧栏标题)
// / interaction_expired(后端已无这次确认/回答的挂起点：把待答卡收口成已中断并解锁输入)；
// / share_revoked(共享被撤销/自行退出：服务端随后以 4404 关闭旁观连接，视图提示并回应用页)；
// 终态 = error / status(idle|error)，busy、status(running) 与 steering_done 都不是。
// message_done 是**轮次收口**而非终态：工具轮在派发工具前也发一次，一次执行可以有多条。
// compacting / compaction_done / notice 三者驱动聊天流里同一枚压缩标记：开始放一枚，终态就地定稿
// （见 settleCompactMarker），刷新后由历史接口的投影补出（ADR 0034）。
// 提问卡与确认卡都画在消息流的调用行上，但**待答**的那次调用在历史里没有 tool 行（工具行要等
// 结果回来才落库），所以两类的待答态都还要 ask_user / confirm_request 事件：重连时 hub 补发
// 同一份，reducer 找不到可附着的行就合成一条无结果调用行（ADR 0029 的「靠行重建」只覆盖已答态）。
import { defineStore } from 'pinia'
import {
  getMessages, getSessionDetail, listModels, listWorkspaces, patchSessionModel,
  fetchAttachmentBlob, uploadAttachment, wsUrl,
} from '../api'
import { newClientMsgId } from '../utils/imageAttachments'
import { findOpenToolCall, DECLINE_ANSWER, NOTICE_TEXT, subrunStepsFromRows } from './messageModel'

// 「执行中被拦下」的三条文案：单点定义，本地拦截与后端 busy 回包共用同一口径
const BUSY_HINT = '会话正在执行中：点右侧「停止」结束当前执行后即可继续发言'
const COMPACT_HINT = '上下文压缩中，请稍候'
const OFFLINE_HINT = '连接未就绪，正在重连，请稍候再发'
const LOAD_FAILED_HINT = '会话加载失败，请再次点击会话重试'

// 会话消息快照只活在本页内存：切回同一会话先摆出来，再后台刷新替换。
// 上限按条数不按字节——单会话首包 ~1 MB（950 KB 来自工具结果），5 个够覆盖来回切换。
const SESSION_CACHE_LIMIT = 5

// 聊天流里那枚未定稿的压缩标记（kind==='compacting'）在哪：从后往前找，同一时刻至多一枚。
// 返回 -1 表示这轮的开始帧没收到（刷新/断线期间压缩跑完），定稿时就当没有标记处理。
function findCompactMarker(store) {
  for (let i = store.messages.length - 1; i >= 0; i--) {
    const m = store.messages[i]
    if (m.role === 'notice' && m.kind === 'compacting') return i
  }
  return -1
}

// 压缩终态：原地把「压缩中」换成定稿记录，而不是再追一条——两条说的是同一次压缩，
// 叠在流里像压了两次。没有标记可换（开始帧没收到）才补一条，保证终态一定看得见。
function settleCompactMarker(store, text, kind) {
  const rec = kind ? { role: 'notice', kind, content: text } : { role: 'notice', content: text }
  const i = findCompactMarker(store)
  if (i >= 0) store.messages.splice(i, 1, rec)
  else store.messages.push(rec)
}

// ---------- 委派卡（ADR-0052 / 规格 2026-11-18-agent-subruns）：subrun_* 事件归并 ----------
// 子 run 事件不带 call_id：同一时刻至多一次 delegate 在跑（工具串行派发），按「还没结果的
// delegate 调用记录」定位即可。子任务行按 index 升序插入——历史重建按 index 排序，到达序
// 若因并行而交错，实时与刷新后的记录就会叉开（ADR-0029 同构）。
function subrunOf(open, index) {
  if (!Number.isInteger(index)) return null
  if (!Array.isArray(open.subruns)) open.subruns = []
  const hit = open.subruns.find((r) => r.index === index)
  if (hit) return hit
  const row = { index, status: 'running' }
  const at = open.subruns.findIndex((r) => r.index > index)
  if (at < 0) open.subruns.push(row)
  else open.subruns.splice(at, 0, row)
  return row
}

function openSubrun(store, index) {
  const open = findOpenToolCall(store.messages, 'delegate')
  return open ? subrunOf(open, index) : null
}

// 合并回执行里的逐项回执：done 事件已经把状态写上去，这里补回执摘要与标题；
// cancelled 的批次没有 subruns 条目——没 done 的行统一收口为已中断（与历史重建同一条规则）。
function mergeDelegateReceipt(open, result) {
  if (!result || typeof result !== 'object') return
  for (const r of Array.isArray(result.subruns) ? result.subruns : []) {
    const idx = Number(r?.index)
    if (!Number.isInteger(idx)) continue
    const row = subrunOf(open, idx)
    row.status = r.status || 'error'
    if (r.title && !row.title) row.title = r.title
    if (r.error != null) row.error = r.error
    if (r.result != null) row.result = r.result
    delete row.progress
  }
  if (result.cancelled) {
    for (const row of open.subruns || []) {
      if (row.status === 'running') row.status = 'cancelled'
      delete row.progress
    }
  }
}

// 本轮结束时要复位的执行态，单点定义：三个终态入口（error / status(idle|error) /
// interaction_expired）共用同一集合。抄漏一项的后果是页面上留一个永远转圈的残块
// （手工走查实测漏过 streamingThinking：卡片收了，「思考中…」还在）。
function endRunState(store) {
  store.running = false
  store.streamingThinking = ''
  store.streamingThinkingOpen = false
  store.compacting = false // 压缩失败也是终态：复位压缩按钮 loading
  store.pendingEcho = ''
  store.pendingOutbox = {}
  store.pendingEchoId = ''
  store.toolProgress = null
  // 没定稿的压缩标记随本轮作废：失败原因已由 error 气泡说明，留一枚永远转圈的标记就是残块
  const pending = findCompactMarker(store)
  if (pending >= 0) store.messages.splice(pending, 1)
}

export function applyEvent(store, ev) {
  switch (ev.type) {
    case 'token': // 流式增量追加
      store.streamingContent += ev.delta
      break
    case 'tool_call':
      store.messages.push({ role: 'tool', name: ev.name, args: ev.args })
      break
    case 'tool_result': {
      // 一次调用在消息流里只占一条记录：结果就地并进「同名、还没结果」的那条调用记录。
      // 历史里一次调用本来就是一条行，实时若拆成两条，刷新前后就会一个调用画两个块。
      const open = findOpenToolCall(store.messages, ev.name)
      if (open) {
        open.result = ev.result
        // 确认标记的使命到此为止：状态已能从结果行的 confirm 字段推出来。留着标记会让实时
        // 记录比历史行多两个键（标记不落库），同构断言就此叉开。
        delete open.awaitingConfirm
        delete open.confirmSummary
        if (ev.name === 'delegate') mergeDelegateReceipt(open, ev.result)
      } else {
        store.messages.push({ role: 'tool', name: ev.name, result: ev.result })
      }
      // 进度只属于「正在跑的那个工具」：同名结果到达即收口，别的工具的结果不误清
      if (!store.toolProgress || store.toolProgress.name === ev.name) store.toolProgress = null
      break
    }
    case 'tool_progress': // 预览工具的临时进度（不落库，刷新即消失；最终结论看 tool_result）
      store.toolProgress = { name: ev.name, line: ev.line }
      break
    // ---- 委派卡：子 run 事件（ADR-0052）。都按 index 归并进打开的 delegate 记录 ----
    case 'subrun_snapshot': {
      // 批量执行中连入（刷新/重连/旁观）：回执行还没落库，历史里没有 delegate 调用行，
      // 只靠事件流卡片会整段消失。没有打开中的记录就合成一条调用行（对齐 confirm_request
      // 合成行做法）；已落库的步骤从历史子 run 行还原——快照只带状态，行一定先于事件落库。
      let open = findOpenToolCall(store.messages, 'delegate')
      if (!open) {
        open = { role: 'tool', name: 'delegate', args: { tasks: ev.tasks || [] } }
        store.messages.push(open)
      }
      const derived = subrunStepsFromRows(store.messages)
      for (const s of ev.subruns || []) {
        if (!Number.isInteger(s?.index)) continue
        const row = subrunOf(open, s.index)
        if (s.title && !row.title) row.title = s.title
        // 快照与实时流有竞态：已收口（completed/error/cancelled）的行绝不被降回运行中。
        // 实时文本/步骤也不被快照覆盖——快照只补缺口，不替换现场。
        if (row.status === 'running') row.status = s.status || 'running'
        if (s.error != null) row.error = s.error
        const d = derived.get(s.index)
        if (d) {
          if (d.texts && !row.texts) row.texts = d.texts
          if (d.steps && !row.steps) row.steps = d.steps
          if (d.result !== undefined && row.result === undefined) row.result = d.result
        }
      }
      break
    }
    case 'subrun_start': {
      const row = openSubrun(store, ev.index)
      if (row && ev.title) row.title = ev.title
      break
    }
    case 'subrun_text': {
      const row = openSubrun(store, ev.index)
      if (row && ev.content) (row.texts || (row.texts = [])).push(ev.content)
      break
    }
    case 'subrun_tool_call': {
      const row = openSubrun(store, ev.index)
      if (!row) break
      const step = { name: ev.name }
      if (ev.args !== undefined) step.args = ev.args
      ;(row.steps || (row.steps = [])).push(step)
      break
    }
    case 'subrun_tool_result': {
      const row = openSubrun(store, ev.index)
      if (!row) break
      const steps = row.steps || (row.steps = [])
      let step = null
      for (let i = steps.length - 1; i >= 0; i--) {
        if (steps[i].name === ev.name && steps[i].result === undefined) { step = steps[i]; break }
      }
      if (!step) { // 孤立结果（乱序/调用行缺失）：与主流程同一处理，另立一条只带结果的步骤
        step = { name: ev.name }
        steps.push(step)
      }
      step.result = ev.result
      if (row.progress && row.progress.name === ev.name) delete row.progress
      break
    }
    case 'subrun_progress': { // 子 run 里的预览进度：临时态，步骤结果/收口即清
      const row = openSubrun(store, ev.index)
      if (row) row.progress = { name: ev.name, line: ev.line }
      break
    }
    case 'subrun_done': {
      const row = openSubrun(store, ev.index)
      if (!row) break
      row.status = ev.status === 'completed' ? 'completed' : 'error'
      if (ev.error != null) row.error = ev.error
      // 停止时整批回执丢失：先完成的子 run 用最后一段文本当回执摘要。
      // 历史重建「没有回执时取收尾文本行」是同一条规则的落库侧写法。
      if (row.status === 'completed' && row.result === undefined
          && Array.isArray(row.texts) && row.texts.length) {
        row.result = row.texts[row.texts.length - 1]
      }
      delete row.progress
      break
    }
    case 'ask_user': {
      // 提问卡画在 ask_user 调用行上（ADR 0029）。实时流里 tool_call 已先把行建好，但行里是
      // 模型原始参数（可能被二次编码）；事件带的是后端规整后的题目，用它覆盖才能保证出卡。
      // 刷新/切会话后历史里**没有**待答调用的 tool 行（工具行要等答案回来才落库），重连补发的
      // 问题只能自己合成一条——丢弃就等于把题目连同唯一的回答入口一起抹掉，用户答不了也停不掉。
      const open = findOpenToolCall(store.messages, 'ask_user')
      if (open) {
        open.args = { questions: ev.questions || [] }
      } else {
        store.messages.push({ role: 'tool', name: 'ask_user', args: { questions: ev.questions || [] } })
      }
      break
    }
    case 'confirm_request': {
      // 事件只作**标记**：卡片内容一律从消息行读（历史里没有事件，靠行才能重建已答态）。
      // 「本次调用需要确认」这件事不在任何行里——它取决于写入确认开关与预览/写库的运行时判据，
      // 前端无从推断，所以待答态仍需这个标记；重连时 hub 会补发同一份，标记因此不会丢。
      const open = findOpenToolCall(store.messages, ev.tool)
      if (open) {
        open.awaitingConfirm = true
        // summary 只服务待答态（hub 补发会一并带回来）；已答态一律回落参数摘要——
        // summary 不落消息行，拿它渲染已答态就会让实时与刷新后叉开。
        open.confirmSummary = ev.summary || ''
      } else {
        // 刷新/切会话回来的路径：待确认的那次调用在历史里**没有** tool 行（工具行要等执行完
        // 才落库），补发的标记没有行可贴。丢弃等于把卡片连同唯一的出口一起抹掉——用户既确认
        // 不了也看不见，runner 的 future 永久挂起。这里合成一条无结果调用行，等价于实时流里
        // 先到的 tool_call（args 事件本身就带着，不必回读 assistant 行的 tool_args）。
        store.messages.push({
          role: 'tool', name: ev.tool, args: ev.args,
          awaitingConfirm: true, confirmSummary: ev.summary || '',
        })
      }
      break
    }
    case 'interaction_expired': {
      // 后端收到 confirm / ask_submit 时已无挂起点（进程重启、或执行早已收口）且该会话不在跑。
      // 不回这一帧的话，卡片永远停在可点状态、点下去零反馈，输入框也一直被执行态锁着。
      // 收口 = 给待答行补一条「已中断」的配对结果：状态从行上推出来，与真实中断留痕同形。
      let closed = false
      for (const m of store.messages) {
        // 「还没结果」两套形状都要认：实时记录用 result，openSession 装入的历史行用 tool_result。
        // 只认 result 会把已答的历史行当成待答，一帧下去答案就被覆盖成已中断。
        if (m.role !== 'tool' || m.result !== undefined || m.tool_result !== undefined) continue
        const openAsk = (m.name ?? m.tool_name) === 'ask_user'
        if (!m.awaitingConfirm && !openAsk) continue
        m.result = { cancelled: true, ...(m.awaitingConfirm ? { confirm: 'cancelled' } : {}) }
        delete m.awaitingConfirm
        delete m.confirmSummary
        closed = true
      }
      endRunState(store)
      store.hint = closed ? '后端已无这次确认/回答的记录（执行已中断或后端重启），卡片已收口' : ''
      break
    }
    case 'message_done': { // 整段回复固化（附带思考留痕）
      const rec = {
        role: 'assistant', content: ev.content, reasoning: store.streamingThinking || undefined,
      }
      // 展开意图在此刻交棒给这条记录：流式块马上卸载，历史块读它当初始态，
      // 正读着的人不会被突然折叠（ADR-0065）。
      // 没点开过就不留键：这个键不落库，历史行永远没有它，多一键就叉开 ADR-0029 的同构。
      if (store.streamingThinkingOpen) rec.reasoning_open = true
      store.messages.push(rec)
      store.streamingContent = ''
      store.streamingThinking = ''
      store.streamingThinkingOpen = false
      store.toolProgress = null // 本轮收口：临时进度不得活到下一条消息
      break
    }
    case 'reasoning_delta': // 思考流式增量（有才发）
      store.streamingThinking += ev.content
      break
    case 'todo_sync': // 待办清单全量替换
      store.todoList = ev.todos || []
      break
    case 'compacting': // 上下文压缩进行中：按钮 loading + 聊天流里立刻放一枚「压缩中」标记
      store.compacting = true
      if (findCompactMarker(store) < 0) {
        store.messages.push({ role: 'notice', kind: 'compacting', content: NOTICE_TEXT.compacting })
      }
      break
    case 'compaction_done': // 压缩完成：复位 + 标记原地定稿
      store.compacting = false
      settleCompactMarker(store, NOTICE_TEXT.compacted, 'compacted')
      break
    case 'notice': // 系统提示（压缩 skip 终态等）：复位压缩态 + 居中灰字提示条
      store.compacting = false
      settleCompactMarker(store, ev.text)
      break
    case 'busy': {
      // 执行中被拒（并发发消息 / 压缩中发消息）的**非终态**提示：绝不得清 running/compacting。
      // 一清红色「停止」按钮就消失，用户既发不出也停不掉，点「重试」再撞同一拒绝 ——
      // 这正是「会话正在执行中 + 重试」死循环的成因（后端因此把这类拒绝发成 busy 而非 error）。
      // 按 client_msg_id 精准回滚对应气泡（插话可有多条在飞），无 id 时退回按 pendingEcho 找最后一条。
      const cid = ev.client_msg_id
      let idx = -1
      if (cid) {
        idx = store.messages.findIndex(
          (m) => m.role === 'user' && m.client_msg_id === cid)
      }
      if (idx < 0 && store.pendingEcho) {
        for (let i = store.messages.length - 1; i >= 0; i--) {
          const m = store.messages[i]
          if (m.role === 'user' && m.content === store.pendingEcho) { idx = i; break }
        }
      }
      if (idx >= 0) {
        const [bubble] = store.messages.splice(idx, 1)
        // outbox 里存了原文字与附件：切走会话后只清 outbox，不把旧会话的草稿回填到新会话
        const key = bubble.client_msg_id || cid
        const out = key ? store.pendingOutbox[key] : null
        if (out) {
          if (out.sessionId === store.currentSessionId) {
            store.rejectedDraft = out.text || ''
            store.rejectedAttachments = out.attachments || []
          }
          delete store.pendingOutbox[key]
        } else {
          store.rejectedDraft = bubble.content || ''
          store.rejectedAttachments = bubble.attachments || []
        }
      }
      if (typeof ev.running === 'boolean') store.running = ev.running
      store.pendingEcho = ''
      store.pendingEchoId = ''
      store.hint = ev.message || BUSY_HINT
      break
    }
    case 'steering_done': { // 插话已在工具边界被模型看见：摘掉「排队中」角标（非终态）
      let idx = -1
      if (ev.client_msg_id) {
        idx = store.messages.findIndex(
          (m) => m.role === 'user' && m.client_msg_id === ev.client_msg_id)
      }
      if (idx < 0) {
        for (let i = store.messages.length - 1; i >= 0; i--) {
          const m = store.messages[i]
          if (m.role === 'user' && m.queued && m.content === ev.content) { idx = i; break }
        }
      }
      if (idx >= 0) store.messages[idx].queued = false
      if (store.pendingEchoId === ev.client_msg_id ||
          (!ev.client_msg_id && store.pendingEcho === ev.content)) {
        store.pendingEcho = '' // 已落库，不再是待回滚的乐观气泡
        store.pendingEchoId = ''
      }
      break
    }
    case 'session_title': // 后端自动命名（首条消息顶掉默认「新会话」）：就地更新侧栏列表项，不等重拉
      for (const list of Object.values(store.sessionsByWs || {})) {
        const hit = list.find((x) => x.id === store.currentSessionId)
        if (hit) {
          hit.title = ev.title
          break
        }
      }
      break
    case 'error':
      store.messages.push({ role: 'error', content: ev.message })
      endRunState(store)
      break
    case 'status': // 终态回执（idle/error）复位执行态；status=running 是重连补发的非终态快照
      if (ev.status === 'running') {
        store.running = true // 后端仍在跑（刷新/重连/另开标签页）：把「停止」按钮立回来
        break // 快照不改进度：那一行还得继续显示
      }
      endRunState(store)
      break
    case 'file_change': // 挂到最近一条 tool 消息，供 diff 渲染（Task 13 最小扩展：补存 path/action 供文件名显示）
      for (let i = store.messages.length - 1; i >= 0; i--) {
        if (store.messages[i].role === 'tool') {
          store.messages[i].diff = ev.diff
          store.messages[i].diff_path = ev.path
          store.messages[i].diff_action = ev.action
          // 记录行 id：聊天流里的内联 diff 靠它跳详情整页。不落库的调用点给 null，
          // 那边就只画 diff 不给跳转入口（不给一个点了没反应的按钮）
          store.messages[i].diff_change_id = ev.change_id ?? null
          break
        }
      }
      break
    case 'share_revoked':
      // 撤销/退出（Ticket 010）：服务端推完这一帧就以 4404 关闭旁观连接。
      // 视图层据此提示「共享已撤销」并回应用页；连接本身由 onclose 的 4404 分支安静收掉。
      store.shareRevoked = true
      endRunState(store)
      break
  }
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    workspaces: [],
    sessionsByWs: {},
    currentSessionId: null,
    currentWorkspaceId: null,
    messages: [],
    loadingSession: false, // 未缓存会话的历史还没到：中栏显加载态，不留上一个会话的残余
    sessionCache: {}, // 会话 id -> { messages, todoList, currentModelId, effectiveModel, visionSupported }
    cacheOrder: [], // 会话 id 的最近使用序：淘汰从头砍，最后一项是最新
    loadedSessionId: null, // 已成功装入过历史的会话：慢响应/失败时据此决定能不能写缓存
    streamingContent: '',
    wsStatus: 'disconnected',
    toolProgress: null, // 预览工具的临时进度 {name, line}：不落库、不进消息流，终态即清
    running: false,
    todoList: [],
    streamingThinking: '',
    streamingThinkingOpen: false, // 本轮思考块是否被手工点开：收口时交给固化后的历史块（ADR-0065）
    models: [],
    currentModelId: null,
    effectiveModel: null, // 实际生效模型（含视觉能力）：由会话详情/切换模型的响应回写
    visionSupported: false,
    compacting: false,
    hint: '', // 执行中被拦下的一次性提示（非终态，不进气泡、不带「重试」）
    rejectedDraft: '', // busy 交还给输入框的草稿：后端没收下这句话，文本不能丢
    rejectedAttachments: [], // 与 rejectedDraft 配套退回输入框的附件，同样不能丢
    pendingEcho: '', // 最近一次本地乐观上行的 user 文本，busy 时按它回滚气泡
    pendingEchoId: '', // 最近一次乐观上行的 client_msg_id，精确回滚/摘角标用
    pendingOutbox: {}, // client_msg_id -> {sessionId, text, attachments}：未被后端确认的乐观消息
    retrying: false, // retry 重传中：防止连点并发重复上传
    shareRevoked: false, // 旁观中共享被撤销/自行退出（Ticket 010）：视图提示并回应用页
  }),
  getters: {
    // 当前会话所属工作区的角色（GET /workspaces 带回，规格 §5.3）：只读旁观据此收口交互。
    // 列表未加载时给 null——渲染按所有者放行，写操作仍由后端兜底。
    currentWorkspaceRole: (state) =>
      state.workspaces.find((w) => w.id === state.currentWorkspaceId)?.role || null,
  },
  actions: {
    async loadWorkspaces() {
      this.workspaces = (await listWorkspaces()).data
    },
    async openSession(id, workspaceId = null) {
      // 离开旧会话：把当前内容留成快照，下次切回先摆出来（只在已成功装入过历史时留）
      if (this.currentSessionId && this.currentSessionId !== id) {
        this._rememberSession(this.currentSessionId)
      }
      // 旧连接必须在等历史之前就断：否则这段等待窗口里它的流事件会写进新会话的消息流，
      // 用户的发言也会被发到上一个会话。新连接仍要等历史装入后才建（见下）。
      this._detachSocket()
      this.currentSessionId = id
      if (workspaceId != null) this.currentWorkspaceId = workspaceId
      // 新会话开一场新旁观：清掉上一次撤销标记，否则同一页里再被撤销时 watch 看不到变化
      this.shareRevoked = false
      this._resetOpenState()
      // 缓存命中就立刻摆出来、不显加载态；未命中则清空残余，让「旧内容在等」不再发生
      const cached = this.sessionCache[id]
      this.messages = cached ? cached.messages : []
      this.todoList = cached ? cached.todoList : []
      this.currentModelId = cached ? cached.currentModelId : null
      this.effectiveModel = cached ? cached.effectiveModel : null
      this.visionSupported = cached ? cached.visionSupported : false
      this.loadingSession = !cached
      // 两个请求并行：详情只补 todo/模型，消息才是首屏；串行会让公网上的等待翻倍。
      // 详情拉取失败不阻断会话打开（T11 审查闭合）：todo/模型降级为空，照常渲染历史并连接
      const detailP = getSessionDetail(id).then((r) => r?.data ?? null).catch(() => null)
      // 竞态守卫：快速切换会话时，先打开会话的慢响应后到，不得覆盖新会话的 messages
      let data = null
      try {
        ({ data } = await getMessages(id))
      } catch {
        data = null // 失败走下面的可重试提示，不把异常抛给点击处理链
      }
      if (this.currentSessionId !== id) return
      if (data) {
        this.messages = data
        this.loadedSessionId = id
        this.loadingSession = false
        // 历史装入之后才连：hub 补发的待答卡若先到，会被随后装入的历史整体替换掉。
        // 角色由后端判（owner→现状 / collaborator→只读旁观，Ticket 008），前端同一入口连接。
        this.connect(id)
      } else {
        this.loadingSession = false
        this.hint = LOAD_FAILED_HINT
        // 历史没装进来就不建新连接（旧连接已在入口断开），点同一会话即可重试
      }
      // 详情慢响应后到同样不得覆盖新会话；todo/模型只回填，不重排消息
      const detail = await detailP
      if (this.currentSessionId !== id) return
      if (detail) {
        this.todoList = detail.todo_state || []
        this.currentModelId = detail.model_config_id ?? null
        this.effectiveModel = detail.effective_model || null
        this.visionSupported = !!detail.effective_model?.supports_vision
        // 已排队但还没被注入的插话没落库（只在 hub.pending），刷新/切回来时由详情带回复原，
        // 否则用户刚说出去的话会凭空消失。旧服务端可能只回字符串，两种形态都要认。
        for (const c of detail.pending_steering || []) {
          if (typeof c === 'string') {
            this.messages.push({ role: 'user', content: c, queued: true })
            continue
          }
          this.messages.push({
            role: 'user', content: c.content || '', attachments: c.attachments || [],
            client_msg_id: c.client_msg_id || undefined, queued: true,
          })
        }
      }
      this._rememberSession(id)
    },
    // 跨会话字段一律复位：它们都属于「刚才那个会话」，跟着新会话走会造出假执行态/假草稿
    _resetOpenState() {
      this.todoList = []
      this.currentModelId = null
      this.effectiveModel = null
      this.visionSupported = false
      this.streamingContent = ''
      this.streamingThinking = ''
      this.streamingThinkingOpen = false
      this.compacting = false
      this.hint = ''
      this.rejectedDraft = ''
      this.rejectedAttachments = []
      this.pendingEcho = ''
      this.pendingEchoId = ''
      this.pendingOutbox = {}
      this.toolProgress = null // 临时进度不跨会话：切会话/重开时一并抹掉
      // 执行态一律以服务端为准，不在这里猜：DB 的 status='running' 在进程崩溃后会滞留
      // 最多 10 分钟（watchdog 周期），拿它立 running=true 会造出「假在跑、又停不掉」的新死结。
      // connect 之后后端若真在跑，会补发一帧 status(running) 把执行态与「停止」按钮立回来。
      this.running = false
      this.loadedSessionId = null
    },
    // 没成功装入过历史的会话不写缓存：否则切回去会先闪一个「空会话」再去拉真历史
    _rememberSession(id) {
      if (!id || this.loadedSessionId !== id) return
      this.sessionCache[id] = {
        messages: this.messages.slice(), // 只拷数组：回来后实时事件还在往原数组 push
        todoList: this.todoList,
        currentModelId: this.currentModelId,
        effectiveModel: this.effectiveModel,
        visionSupported: this.visionSupported,
      }
      this.cacheOrder = this.cacheOrder.filter((x) => x !== id).concat(id)
      while (this.cacheOrder.length > SESSION_CACHE_LIMIT) {
        delete this.sessionCache[this.cacheOrder.shift()]
      }
    },
    forgetSession(id) {
      delete this.sessionCache[id]
      this.cacheOrder = this.cacheOrder.filter((x) => x !== id)
    },
    // 断开当前连接：切会话/重连前必须先摘干净，否则旧会话的流事件会写进新会话的消息流
    _detachSocket() {
      if (this._ws) {
        this._ws.onmessage = null
        this._ws.onclose = null // 主动断开不触发旧连接的重连回调
        this._ws.close()
        this._ws = null
      }
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer)
        this._reconnectTimer = null
      }
      this.wsStatus = 'disconnected'
    },
    connect(sessionId, keepBackoff = false) {
      this._detachSocket()
      // 入口重置退避计数：用户发起的新连接（切换会话/手动重连）不沿用旧退避；
      // 定时器重连传 keepBackoff=true，以延续同一断线期的指数退避（1s→2s→4s→…上限 15s）
      if (!keepBackoff) this._retry = 0
      this.currentSessionId = sessionId
      const ws = new WebSocket(wsUrl(`/ws/chat/${sessionId}`))
      this._ws = ws
      ws.onopen = () => {
        this.wsStatus = 'connected'
        this._retry = 0 // 连接成功后退避归零
      }
      ws.onmessage = (e) => applyEvent(this, JSON.parse(e.data))
      ws.onclose = (ev) => {
        this.wsStatus = 'disconnected'
        // WS 认证收口（Ticket 009）：4401=未登录拒→登录页；4404=会话不可用（被删/越权）。
        // 都不是网络抖动，落进退避重连会无限重复被拒的握手。
        if (ev?.code === 4401) { window.location.assign('/login'); return }
        if (ev?.code === 4404) return
        // 断线指数退避重连：1s→2s→4s→…上限 15s
        const delay = Math.min(1000 * 2 ** (this._retry || 0), 15000)
        this._retry = (this._retry || 0) + 1
        this._reconnectTimer = setTimeout(() => this.connect(sessionId, true), delay)
      }
    },
    // 上行辅助：连接就绪才发送，返回是否真正发出。
    // false = 未发送（断线/重连窗口期），调用方必须保留待处理状态，供连接恢复后重试
    _uplink(payload) {
      // readyState 1 即 WebSocket.OPEN（用字面量 1，避免 node 测试环境无 WebSocket 全局）
      if (this._ws && this._ws.readyState === 1) {
        this._ws.send(JSON.stringify(payload))
        return true
      }
      console.warn('[chat] WS 未就绪，上行丢弃：', payload.type)
      return false
    },
    send(payload) {
      const { text = '', attachments = [] } =
        typeof payload === 'string' ? { text: payload } : (payload || {})
      const content = (text || '').trim()
      if (!content && !attachments.length) return false
      // 手动压缩中没有"下一轮边界"可注入，后端也只会回 busy —— 本地先拦下，文本留在框里。
      // running 为真时不拦：那是自动压缩（跑在 run 内部），插话本该进后端的插话队列。
      if (this.compacting && !this.running) {
        this.rejectedDraft = content
        this.hint = COMPACT_HINT
        return false
      }
      // 非视觉模型带图会被后端拒或把图片丢掉：本地先拦下，既不产生气泡也不进 outbox
      if (attachments.length && !this.visionSupported) {
        this.hint = '当前模型不支持图片输入，请切换支持视觉的模型'
        return false
      }
      // 执行中发出 = 插话（steering）：照样上行，气泡先挂「排队中」角标，
      // 后端在下一个工具边界落库注入并回 steering_done 时摘掉
      const clientMsgId = newClientMsgId()
      const steering = this.running
      const bubble = { role: 'user', content, attachments, client_msg_id: clientMsgId }
      if (steering) bubble.queued = true
      this.messages.push(bubble)
      // outbox 记录这条乐观消息的文字与附件：busy 回滚时按当前会话决定是否退回输入框
      this.pendingOutbox[clientMsgId] = {
        sessionId: this.currentSessionId, text: content, attachments,
      }
      this.pendingEcho = content // busy 时按它回滚这条乐观气泡（后端可能没收下）
      this.pendingEchoId = clientMsgId
      this.streamingContent = ''
      this.streamingThinking = ''
      this.streamingThinkingOpen = false
      this.hint = ''
      this.rejectedDraft = ''
      this.running = true
      if (!this._uplink({
        type: 'user_message', content,
        attachment_ids: attachments.map((a) => a.id),
        client_msg_id: clientMsgId,
      })) {
        // 上行即失败（readyState 检查后瞬间断线）：气泡与执行态一并回滚，
        // 不留「已发出」的假象；文字与附件回到 rejectedDraft/rejectedAttachments 供连接恢复后再发
        this.messages.pop()
        delete this.pendingOutbox[clientMsgId]
        this.pendingEcho = ''
        this.pendingEchoId = ''
        this.running = false
        this.rejectedDraft = content
        this.rejectedAttachments = attachments
        this.hint = OFFLINE_HINT
        return false
      }
      return true
    },
    submitAsk(answers) {
      // 旧 ask_answer/ask_choice 已随后端删除；单题协议下 answers 恒为一项。
      // 卡片状态由结果行到达时翻转，本地不清任何字段：上行失败时卡片自然还在，用户可重试。
      if (!this._uplink({ type: 'ask_submit', answers })) this.hint = OFFLINE_HINT
    },
    declineAsk() {
      // 「不再提问，自行决定」：不是取消执行，而是把决定权交还模型并继续本轮。
      // 固定答复即协议，常量单点定义在 messageModel.js——已答态靠它逐字识别并标注「自行决定」。
      // declined 是给后端的机器标记（ADR-0032）：需求澄清门禁据此解除本会话的强制澄清。
      // 后端不拿上面那段文案当判据——真源在前端常量里，改一个字就会静默失效。
      const answers = [{ answer: DECLINE_ANSWER }]
      if (!this._uplink({ type: 'ask_submit', answers, declined: true })) this.hint = OFFLINE_HINT
    },
    confirm(approved) {
      // 卡片状态由结果行里的 confirm 字段翻转，本地不清任何字段：上行失败时卡片自然还在，
      // 但必须在输入框留一句原因——否则点下去什么也不发生，用户无从判断是没点上还是后端没了。
      if (!this._uplink({ type: 'confirm', approved })) this.hint = OFFLINE_HINT
    },
    async requestCompact() {
      this._uplink({ type: 'compact' })
    },
    cancel() {
      this._uplink({ type: 'cancel' })
      this.toolProgress = null // 用户点了「停止」：那行进度当场作废，不等后端回执
      // 后端 hub.cancel 会一并丢弃未注入的插话（不落库）：本地同步摘掉那些「排队中」气泡，
      // 文本与附件按原顺序退回输入框，别让用户以为说出去了
      const stranded = this.messages.filter((m) => m.role === 'user' && m.queued)
      if (stranded.length) {
        this.messages = this.messages.filter((m) => !(m.role === 'user' && m.queued))
        const texts = stranded.map((m) => m.content).filter(Boolean)
        this.rejectedDraft = texts.join('\n\n')
        this.rejectedAttachments = stranded.flatMap((m) => m.attachments || [])
        this.hint = `本轮已停止：${stranded.length} 条插话未送达，文本与图片已退回输入框`
      }
      this.pendingOutbox = {}
      this.pendingEcho = ''
      this.pendingEchoId = ''
      this.running = false
      this.streamingThinking = ''
      this.streamingThinkingOpen = false
    },
    async retry() {
      // 重发最近一条用户消息；带图的重试必须先重新上传，拿到新附件 ID 再发
      if (this.retrying) return
      let last = null
      for (let i = this.messages.length - 1; i >= 0; i--) {
        if (this.messages[i].role === 'user') { last = this.messages[i]; break }
      }
      if (!last) return
      const text = last.content || ''
      const atts = last.attachments || []
      if (!atts.length) { this.send(text); return }
      const sid = this.currentSessionId
      this.retrying = true
      try {
        const fresh = []
        for (const a of atts) {
          try {
            const { data: blob } = await fetchAttachmentBlob(a.url)
            const { data } = await uploadAttachment(sid, blob, a.filename)
            fresh.push({
              id: data.id, filename: data.filename, mime: data.mime,
              size_bytes: data.size_bytes, url: data.url,
            })
          } catch {
            // 等重传期间切走会话：旧会话的草稿不得回填到新会话
            if (this.currentSessionId !== sid) return
            this.rejectedDraft = text
            this.rejectedAttachments = atts
            this.hint = '图片重新上传失败，请重新粘贴后再发'
            return
          }
          if (this.currentSessionId !== sid) return
        }
        if (!this.send({ text, attachments: fresh })) {
          this.rejectedDraft = text
          this.rejectedAttachments = fresh
        }
      } finally {
        this.retrying = false
      }
    },
    async loadModels() {
      // 只要对话档案：kind='embedding' 的向量模型产不出文本，进下拉就是「选中即报错」（Ticket 005）
      this.models = (await listModels('chat')).data
    },
    async setModel(id) {
      const sid = this.currentSessionId
      const { data } = await patchSessionModel(sid, id)
      // 回写会话校验（T11 审查闭合）：await 期间用户可能已切换会话，仅当前仍是发起会话才回写，防陈旧覆盖
      if (this.currentSessionId !== sid) return
      this.currentModelId = id
      // 切换后的实际生效模型可能落回全局默认：视觉能力位必须按响应回写
      if (data?.effective_model) {
        this.effectiveModel = data.effective_model
        this.visionSupported = !!data.effective_model.supports_vision
      }
    },
  },
})
