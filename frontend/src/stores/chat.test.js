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
})
