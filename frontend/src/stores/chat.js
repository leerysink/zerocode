// chat store：会话/消息/待办/模型/WS 状态。事件处理抽为导出的纯函数 applyEvent(store, ev)，
// 便于 vitest 不经组件直接调用（connect 的 onmessage 只做 applyEvent(this, JSON.parse(e.data))）。
// 下行事件类型与后端 loop.py / hub.py 协议一致：
// token / reasoning_delta / tool_call / tool_result / ask_user(多问题) / confirm_request / message_done
// / error / status / file_change / todo_sync / compacting / compaction_done / notice(压缩 skip 终态)
// / busy(执行中被拒的非终态提示) / steering_done(插话已在工具边界被模型看见)
// / session_title(首条消息自动命名，就地更新侧栏标题)；
// 终态 = error / status(idle|error)，busy、status(running) 与 steering_done 都不是。
import { defineStore } from 'pinia'
import {
  getMessages, getSessionDetail, listModels, listWorkspaces, patchSessionModel,
  fetchAttachmentBlob, uploadAttachment, wsUrl,
} from '../api'
import { newClientMsgId } from '../utils/imageAttachments'

// 「执行中被拦下」的三条文案：单点定义，本地拦截与后端 busy 回包共用同一口径
const BUSY_HINT = '会话正在执行中：点右侧「停止」结束当前执行后即可继续发言'
const COMPACT_HINT = '上下文压缩中，请稍候'
const OFFLINE_HINT = '连接未就绪，正在重连，请稍候再发'

export function applyEvent(store, ev) {
  switch (ev.type) {
    case 'token': // 流式增量追加
      store.streamingContent += ev.delta
      break
    case 'tool_call':
      store.messages.push({ role: 'tool', name: ev.name, args: ev.args })
      break
    case 'tool_result':
      store.messages.push({ role: 'tool', name: ev.name, result: ev.result })
      break
    case 'ask_user': // 多问题新协议：questions 逐题渲染（AskPanel 消费）
      store.pendingAsk = { questions: ev.questions || [] }
      break
    case 'confirm_request':
      store.pendingConfirm = { tool: ev.tool, args: ev.args }
      break
    case 'message_done': // 整段回复固化（附带思考留痕）
      store.messages.push({ role: 'assistant', content: ev.content, reasoning: store.streamingThinking || undefined })
      store.streamingContent = ''
      store.streamingThinking = ''
      break
    case 'reasoning_delta': // 思考流式增量（有才发）
      store.streamingThinking += ev.content
      break
    case 'todo_sync': // 待办清单全量替换
      store.todoList = ev.todos || []
      break
    case 'compacting': // 上下文压缩进行中
      store.compacting = true
      break
    case 'compaction_done': // 压缩完成：复位 + 消息流提示条
      store.compacting = false
      store.messages.push({ role: 'notice', content: '已压缩上下文：更早对话已替换为摘要，最近几轮保留原文' })
      break
    case 'notice': // 系统提示（压缩 skip 终态等）：复位压缩态 + 居中灰字提示条
      store.compacting = false
      store.messages.push({ role: 'notice', content: ev.text })
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
      store.running = false
      store.streamingThinking = ''
      store.compacting = false // 压缩失败也是终态：复位压缩按钮 loading
      store.pendingEcho = ''
      store.pendingOutbox = {}
      store.pendingEchoId = ''
      break
    case 'status': // 终态回执（idle/error）复位执行态；status=running 是重连补发的非终态快照
      if (ev.status === 'running') {
        store.running = true // 后端仍在跑（刷新/重连/另开标签页）：把「停止」按钮立回来
        break
      }
      store.running = false
      store.streamingThinking = ''
      store.compacting = false
      store.pendingEcho = ''
      store.pendingOutbox = {}
      store.pendingEchoId = ''
      break
    case 'file_change': // 挂到最近一条 tool 消息，供 diff 渲染（Task 13 最小扩展：补存 path/action 供文件名显示）
      for (let i = store.messages.length - 1; i >= 0; i--) {
        if (store.messages[i].role === 'tool') {
          store.messages[i].diff = ev.diff
          store.messages[i].diff_path = ev.path
          store.messages[i].diff_action = ev.action
          break
        }
      }
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
    streamingContent: '',
    wsStatus: 'disconnected',
    pendingAsk: null,
    pendingConfirm: null,
    running: false,
    todoList: [],
    streamingThinking: '',
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
  }),
  actions: {
    async loadWorkspaces() {
      this.workspaces = (await listWorkspaces()).data
    },
    async openSession(id, workspaceId = null) {
      this.currentSessionId = id
      // 竞态守卫：快速切换会话时，先打开会话的慢响应后到，不得覆盖新会话的 messages
      const { data } = await getMessages(id)
      if (this.currentSessionId !== id) return
      // 会话详情：todo/模型状态（交互增强）；detail 慢响应后到同样不得覆盖新会话。
      // 详情拉取失败不阻断会话打开（T11 审查闭合）：todo/模型降级为空，照常渲染历史并连接
      let detail = null
      try {
        detail = (await getSessionDetail(id)).data
      } catch {
        // 降级：detail 缺失视同空 todo / 未配置模型
      }
      if (this.currentSessionId !== id) return
      this.todoList = detail?.todo_state || []
      this.currentModelId = detail?.model_config_id ?? null
      this.effectiveModel = detail?.effective_model || null
      this.visionSupported = !!detail?.effective_model?.supports_vision
      this.streamingThinking = ''
      this.compacting = false
      this.hint = ''
      this.rejectedDraft = ''
      this.rejectedAttachments = []
      this.pendingEcho = ''
      this.pendingEchoId = ''
      this.pendingOutbox = {}
      // 渲染门：右栏（Task 13/14）以 currentWorkspaceId 判定；由调用方传入工作区 id
      if (workspaceId != null) this.currentWorkspaceId = workspaceId
      this.messages = data
      // 已排队但还没被注入的插话没落库（只在 hub.pending），刷新/切回来时由详情带回复原，
      // 否则用户刚说出去的话会凭空消失。旧服务端可能只回字符串，两种形态都要认。
      for (const c of detail?.pending_steering || []) {
        if (typeof c === 'string') {
          this.messages.push({ role: 'user', content: c, queued: true })
          continue
        }
        this.messages.push({
          role: 'user', content: c.content || '', attachments: c.attachments || [],
          client_msg_id: c.client_msg_id || undefined, queued: true,
        })
      }
      this.streamingContent = ''
      this.pendingAsk = null
      this.pendingConfirm = null
      // 执行态一律以服务端为准，不在这里猜：DB 的 status='running' 在进程崩溃后会滞留
      // 最多 10 分钟（watchdog 周期），拿它立 running=true 会造出「假在跑、又停不掉」的新死结。
      // connect 之后后端若真在跑，会补发一帧 status(running) 把执行态与「停止」按钮立回来。
      this.running = false
      this.connect(id)
    },
    connect(sessionId, keepBackoff = false) {
      if (this._ws) {
        this._ws.onclose = null // 切换会话/重连前断开旧连接，不触发其重连回调
        this._ws.close()
      }
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer)
        this._reconnectTimer = null
      }
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
      // 压缩中没有"下一轮边界"可注入，后端也只会回 busy —— 本地先拦下，文本留在框里
      if (this.compacting) {
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
      // 旧 ask_answer/ask_choice 已随后端删除；ask_submit 一次提交全部答案。
      // 仅上行成功才清卡片；失败保留，连接恢复后用户可重试同一操作
      if (this._uplink({ type: 'ask_submit', answers })) this.pendingAsk = null
    },
    confirm(approved) {
      if (this._uplink({ type: 'confirm', approved })) this.pendingConfirm = null
    },
    async requestCompact() {
      this._uplink({ type: 'compact' })
    },
    cancel() {
      this._uplink({ type: 'cancel' })
      this.pendingAsk = null
      this.pendingConfirm = null
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
