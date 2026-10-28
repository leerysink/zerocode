// @vitest-environment happy-dom
import { describe, it, expect, vi } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  getMessages: vi.fn(), getSessionDetail: vi.fn(), listModels: vi.fn(),
  listWorkspaces: vi.fn(), patchSessionModel: vi.fn(), wsUrl: vi.fn(() => 'ws://t'),
}))

import ChatMessages from './ChatMessages.vue'

const ImageStub = {
  name: 'a-image',
  props: ['src', 'alt', 'width'],
  render() {
    return h('img', { class: 'stub-image', src: this.src, alt: this.alt })
  },
}
const GroupStub = {
  name: 'a-image-preview-group',
  render() {
    return h('div', { class: 'stub-image-group' }, this.$slots.default ? this.$slots.default() : [])
  },
}
const ButtonStub = {
  name: 'a-button',
  render() {
    return h('button', this.$slots.default ? this.$slots.default() : [])
  },
}

function mountMessages(messages) {
  setActivePinia(createPinia())
  return mount(ChatMessages, {
    props: { messages, hasSession: true },
    global: {
      stubs: {
        'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
        MarkdownView: true, ToolCallBlock: true, AskUserCard: true,
        ThinkingBlock: true, TodoCard: true,
      },
    },
  })
}

it('user 气泡在文字之前渲染附件图片', () => {
  const w = mountMessages([{
    role: 'user', content: '这是什么？',
    attachments: [
      { id: 1, filename: 'a.png', url: '/api/attachments/1' },
      { id: 2, filename: 'b.png', url: '/api/attachments/2' },
    ],
  }])
  const imgs = w.findAll('.zc-bubble-images img')
  expect(imgs.length).toBe(2)
  expect(imgs[0].attributes('src')).toBe('/api/attachments/1')
  expect(w.find('.zc-bubble-text').text()).toBe('这是什么？')
  // 图片容器在文字之前
  expect(w.find('.zc-bubble').html().indexOf('zc-bubble-images'))
    .toBeLessThan(w.find('.zc-bubble').html().indexOf('zc-bubble-text'))
})

it('纯图片消息不渲染空白文字行', () => {
  const w = mountMessages([{
    role: 'user', content: '',
    attachments: [{ id: 1, filename: 'a.png', url: '/api/attachments/1' }],
  }])
  expect(w.find('.zc-bubble-images').exists()).toBe(true)
  expect(w.find('.zc-bubble-text').exists()).toBe(false)
})

it('排队角标与图片共存', () => {
  const w = mountMessages([{
    role: 'user', content: '', queued: true,
    attachments: [{ id: 1, filename: 'a.png', url: '/api/attachments/1' }],
  }])
  expect(w.find('.zc-queued').exists()).toBe(true)
  expect(w.find('.zc-bubble-images').exists()).toBe(true)
})
