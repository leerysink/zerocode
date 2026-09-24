// applyEvent 纯函数测试：传入普通对象即可（不依赖 pinia/WS）
import { describe, it, expect } from 'vitest'
import { applyEvent } from './chat'

function mk() {
  return { messages: [], streamingContent: '', streamingThinking: '', todoList: [], compacting: false, pendingAsk: null }
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
