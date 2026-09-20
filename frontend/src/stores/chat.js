// chat store：会话/消息/待办/模型/WS 状态。事件处理抽为导出的纯函数 applyEvent(store, ev)，
// 便于 vitest 不经组件直接调用（connect 的 onmessage 只做 applyEvent(this, JSON.parse(e.data))）。
// 下行事件类型与后端 loop.py / hub.py 协议一致：
// token / reasoning_delta / tool_call / tool_result / ask_user(多问题) / confirm_request / message_done
// / error / status / file_change / todo_sync / compacting / compaction_done / notice(压缩 skip 终态)
// / busy(执行中被拒的非终态提示)；终态 = error / status(idle|error)，busy 与 status(running) 都不是。
import { defineStore } from 'pinia'
import { getMessages, getSessionDetail, listModels, listWorkspaces, patchSessionModel, wsUrl } from '../api'

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
      const last = store.messages[store.messages.length - 1]
      if (last && last.role === 'user' && last.content === store.pendingEcho) {
        store.messages.pop() // 后端根本没落库：回滚乐观气泡，免得刷新后凭空消失更迷惑
        store.rejectedDraft = store.pendingEcho // 文本交回输入框，用户不必重打
      }
      store.pendingEcho = ''
      store.hint = ev.message || BUSY_HINT
      break
    }
    case 'error':
      store.messages.push({ role: 'error', content: ev.message })
      store.running = false
      store.streamingThinking = ''
      store.compacting = false // 压缩失败也是终态：复位压缩按钮 loading
      store.pendingEcho = ''
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
    compacting: false,
    hint: '', // 执行中被拦下的一次性提示（非终态，不进气泡、不带「重试」）
    rejectedDraft: '', // busy 交还给输入框的草稿：后端没收下这句话，文本不能丢
    pendingEcho: '', // 最近一次本地乐观上行的 user 文本，busy 时按它回滚气泡
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
      this.streamingThinking = ''
      this.compacting = false
      this.hint = ''
      this.rejectedDraft = ''
      this.pendingEcho = ''
      // 渲染门：右栏（Task 13/14）以 currentWorkspaceId 判定；由调用方传入工作区 id
      if (workspaceId != null) this.currentWorkspaceId = workspaceId
      this.messages = data
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
      const ws = new WebSocket(wsUrl(sessionId))
      this._ws = ws
      ws.onopen = () => {
        this.wsStatus = 'connected'
        this._retry = 0 // 连接成功后退避归零
      }
      ws.onmessage = (e) => applyEvent(this, JSON.parse(e.data))
      ws.onclose = () => {
        this.wsStatus = 'disconnected'
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
    send(text) {
      const content = (text || '').trim()
      if (!content) return false
      // 执行中/压缩中：本地就拦下，不再"发了再撞后端拒绝"——那句 error 曾被前端当终态，
      // 清掉 running 后「停止」按钮消失，用户进退两难。文本留在框里，用户看得到原因。
      if (this.running || this.compacting) {
        this.rejectedDraft = content
        this.hint = this.running ? BUSY_HINT : COMPACT_HINT
        return false
      }
      this.messages.push({ role: 'user', content })
      this.pendingEcho = content // busy 时按它回滚这条乐观气泡（后端可能根本没收下）
      this.streamingContent = ''
      this.streamingThinking = ''
      this.hint = ''
      this.rejectedDraft = ''
      this.running = true
      if (!this._uplink({ type: 'user_message', content })) {
        // 上行即失败（readyState 检查后瞬间断线）：气泡与执行态一并回滚，
        // 不留「已发出」的假象；文本回到 rejectedDraft 供连接恢复后再发
        this.messages.pop()
        this.pendingEcho = ''
        this.running = false
        this.rejectedDraft = content
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
      this.running = false
      this.streamingThinking = ''
    },
    retry() {
      // 重发最近一条用户消息
      for (let i = this.messages.length - 1; i >= 0; i--) {
        if (this.messages[i].role === 'user') {
          this.send(this.messages[i].content)
          return
        }
      }
    },
    async loadModels() {
      this.models = (await listModels()).data
    },
    async setModel(id) {
      const sid = this.currentSessionId
      await patchSessionModel(sid, id)
      // 回写会话校验（T11 审查闭合）：await 期间用户可能已切换会话，仅当前仍是发起会话才回写，防陈旧覆盖
      if (this.currentSessionId === sid) this.currentModelId = id
    },
  },
})
