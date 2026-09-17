// chat store：会话/消息/WS 状态。事件处理抽为导出的纯函数 applyEvent(store, ev)，
// 便于 vitest 不经组件直接调用（connect 的 onmessage 只做 applyEvent(this, JSON.parse(e.data))）。
// 下行事件类型与后端 loop.py / hub.py 协议一致：
// token / tool_call / tool_result / ask_user / confirm_request / message_done / error / status / file_change
import { defineStore } from 'pinia'
import { getMessages, listWorkspaces, wsUrl } from '../api'

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
    case 'ask_user':
      store.pendingAsk = { question: ev.question, options: ev.options || [] }
      break
    case 'confirm_request':
      store.pendingConfirm = { tool: ev.tool, args: ev.args }
      break
    case 'message_done': // 整段回复固化
      store.messages.push({ role: 'assistant', content: ev.content })
      store.streamingContent = ''
      break
    case 'error':
      store.messages.push({ role: 'error', content: ev.message })
      store.running = false
      break
    case 'status': // 终态回执（idle/error），复位执行态
      store.running = false
      break
    case 'file_change': // 挂到最近一条 tool 消息，供 diff 渲染
      for (let i = store.messages.length - 1; i >= 0; i--) {
        if (store.messages[i].role === 'tool') {
          store.messages[i].diff = ev.diff
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
  }),
  actions: {
    async loadWorkspaces() {
      this.workspaces = (await listWorkspaces()).data
    },
    async openSession(id) {
      this.currentSessionId = id
      this.messages = (await getMessages(id)).data
      this.streamingContent = ''
      this.pendingAsk = null
      this.pendingConfirm = null
      this.running = false
      this.connect(id)
    },
    connect(sessionId) {
      if (this._ws) {
        this._ws.onclose = null // 切换会话/重连前断开旧连接，不触发其重连回调
        this._ws.close()
      }
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer)
        this._reconnectTimer = null
      }
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
        this._reconnectTimer = setTimeout(() => this.connect(sessionId), delay)
      }
    },
    // 上行辅助：仅连接就绪时发送（ws.readyState === 1 即 OPEN）
    _uplink(payload) {
      if (this._ws && this._ws.readyState === 1) this._ws.send(JSON.stringify(payload))
    },
    send(text) {
      const content = (text || '').trim()
      if (!content || !this._ws || this._ws.readyState !== 1) return
      this.messages.push({ role: 'user', content })
      this.streamingContent = ''
      this.running = true
      this._uplink({ type: 'user_message', content })
    },
    answerAsk(text) {
      this._uplink({ type: 'ask_answer', text })
      this.pendingAsk = null
    },
    chooseAsk(option) {
      this._uplink({ type: 'ask_choice', option })
      this.pendingAsk = null
    },
    confirm(approved) {
      this._uplink({ type: 'confirm', approved })
      this.pendingConfirm = null
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
  },
})
