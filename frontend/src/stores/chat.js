// chat store：会话/消息/待办/模型/WS 状态。事件处理抽为导出的纯函数 applyEvent(store, ev)，
// 便于 vitest 不经组件直接调用（connect 的 onmessage 只做 applyEvent(this, JSON.parse(e.data))）。
// 下行事件类型与后端 loop.py / hub.py 协议一致：
// token / reasoning_delta / tool_call / tool_result / ask_user(多问题) / confirm_request / message_done
// / error / status / file_change / todo_sync / compacting / compaction_done
import { defineStore } from 'pinia'
import { getMessages, getSessionDetail, listModels, listWorkspaces, patchSessionModel, wsUrl } from '../api'

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
    case 'error':
      store.messages.push({ role: 'error', content: ev.message })
      store.running = false
      store.streamingThinking = ''
      break
    case 'status': // 终态回执（idle/error），复位执行态
      store.running = false
      store.streamingThinking = ''
      store.compacting = false
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
      // 会话详情：todo/模型状态（交互增强）；detail 慢响应后到同样不得覆盖新会话
      const detail = (await getSessionDetail(id)).data
      if (this.currentSessionId !== id) return
      this.todoList = detail.todo_state || []
      this.currentModelId = detail.model_config_id ?? null
      this.streamingThinking = ''
      this.compacting = false
      // 渲染门：右栏（Task 13/14）以 currentWorkspaceId 判定；由调用方传入工作区 id
      if (workspaceId != null) this.currentWorkspaceId = workspaceId
      this.messages = data
      this.streamingContent = ''
      this.pendingAsk = null
      this.pendingConfirm = null
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
      if (!content || !this._ws || this._ws.readyState !== 1) return
      this.messages.push({ role: 'user', content })
      this.streamingContent = ''
      this.running = true
      this._uplink({ type: 'user_message', content })
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
      await patchSessionModel(this.currentSessionId, id)
      this.currentModelId = id
    },
  },
})
