import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore, applyEvent } from '../src/stores/chat'

describe('chat store 事件流', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('token 追加 streamingContent，message_done 固化', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'token', delta: '你' })
    applyEvent(s, { type: 'token', delta: '好' })
    expect(s.streamingContent).toBe('你好')
    applyEvent(s, { type: 'message_done', content: '你好' })
    expect(s.streamingContent).toBe('')
    expect(s.messages.at(-1)).toMatchObject({ role: 'assistant', content: '你好' })
  })
  it('tool_call/tool_result 成对追加，ask_user/confirm 置 pending', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'tool_result', name: 'write_file', result: { ok: true } })
    expect(s.messages.filter(m => m.role === 'tool')).toHaveLength(2)
    applyEvent(s, { type: 'ask_user', question: '选哪个', options: ['A'] })
    expect(s.pendingAsk).toMatchObject({ question: '选哪个' })
    applyEvent(s, { type: 'confirm_request', tool: 'write_file' })
    expect(s.pendingConfirm).toBeTruthy()
  })
  it('error 追加错误消息并复位 running', () => {
    const s = useChatStore()
    s.running = true
    applyEvent(s, { type: 'error', message: 'boom' })
    expect(s.messages.at(-1)).toMatchObject({ role: 'error', content: 'boom' })
    expect(s.running).toBe(false)
  })
  it('file_change 事件挂到最近 tool 消息', () => {
    const s = useChatStore()
    applyEvent(s, { type: 'tool_call', name: 'write_file', args: { path: 'a.txt' } })
    applyEvent(s, { type: 'file_change', path: 'a.txt', action: 'create', diff: '--- a/a.txt' })
    const tool = s.messages.filter(m => m.role === 'tool').at(-1)
    expect(tool.diff).toBe('--- a/a.txt')
  })
})
