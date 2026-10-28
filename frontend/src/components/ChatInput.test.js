// @vitest-environment happy-dom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// chat store 会从 '../api' 取一批命名导出：mock 工厂必须把它们都补上，
// 否则 vitest 在解析 store 模块时直接报「No export defined on mock」。
vi.mock('../api', () => ({
  tree: vi.fn().mockResolvedValue({ data: [] }),
  listModels: vi.fn().mockResolvedValue({ data: [] }),
  getMessages: vi.fn(),
  getSessionDetail: vi.fn(),
  listWorkspaces: vi.fn(),
  patchSessionModel: vi.fn(),
  fetchAttachmentBlob: vi.fn(),
  uploadAttachment: vi.fn(),
  wsUrl: vi.fn(() => 'ws://t'),
}))

// vi.hoisted 先于 import 执行，此时拿不到 Vue 的 ref/computed：
// 先占位一个空对象，再在 mock 工厂里（模块加载时）填充真实响应式引用。
const attState = vi.hoisted(() => ({}))
vi.mock('../composables/useImageAttachments', async () => {
  const { ref, computed } = await import('vue')
  attState.items = ref([])
  attState.error = ref('')
  attState.addFiles = vi.fn()
  attState.remove = vi.fn()
  attState.retry = vi.fn()
  attState.clear = vi.fn()
  attState.mergeRejected = vi.fn()
  return {
    useImageAttachments: () => ({
      items: attState.items,
      error: attState.error,
      count: computed(() => attState.items.value.length),
      uploading: computed(() => attState.items.value.some((i) => i.status === 'uploading')),
      hasFailed: computed(() => attState.items.value.some((i) => i.status === 'failed')),
      addFiles: attState.addFiles,
      remove: attState.remove,
      retry: attState.retry,
      clear: attState.clear,
      readyItems: () => attState.items.value.filter((i) => i.status === 'ready'),
      mergeRejected: attState.mergeRejected,
    }),
  }
})

import ChatInput from './ChatInput.vue'
import { useChatStore } from '../stores/chat'

const ButtonStub = {
  name: 'a-button',
  props: ['disabled', 'loading'],
  render() {
    return h('button', {
      class: ['stub-btn', { 'is-disabled': !!this.disabled }],
      disabled: !!this.disabled,
      onClick: (e) => this.$emit('click', e),
    }, this.$slots.default ? this.$slots.default() : [])
  },
}
const TextareaStub = {
  name: 'a-textarea',
  props: ['value'],
  emits: ['update:value'],
  render() {
    return h('textarea', {
      value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value),
    })
  },
}

function mountInput() {
  setActivePinia(createPinia())
  return mount(ChatInput, {
    props: { running: false },
    global: {
      stubs: {
        'a-button': ButtonStub, 'a-textarea': TextareaStub,
        'a-select': true, 'a-select-option': true, 'a-spin': true,
        ChatAppActions: true, 'chat-app-actions': true,
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  attState.items.value = []
  attState.error.value = ''
})

it('粘贴图片调用 addFiles', async () => {
  const w = mountInput()
  const f = new File(['x'], 'a.png', { type: 'image/png' })
  await w.find('.zc-input').trigger('paste', {
    clipboardData: { files: [f], types: ['Files'] },
  })
  expect(attState.addFiles).toHaveBeenCalledWith([f])
})

it('纯文字粘贴不拦截图片入口', async () => {
  const w = mountInput()
  await w.find('.zc-input').trigger('paste', {
    clipboardData: { files: [], types: ['text/plain'] },
  })
  expect(attState.addFiles).not.toHaveBeenCalled()
})

it('上传中或非视觉模型带附件时发送按钮禁用', async () => {
  const w = mountInput()
  attState.items.value = [{ key: 'a', status: 'uploading' }]
  await nextTick()
  expect(w.find('.zc-send-btn').attributes('disabled')).toBeDefined()

  attState.items.value = [{ key: 'b', status: 'ready', id: 1, url: '/api/attachments/1' }]
  await nextTick()
  expect(w.find('.zc-send-btn').attributes('disabled')).toBeDefined()
  expect(w.text()).toContain('当前模型不支持图片输入')
})

it('发送成功清空文字与附件，失败保留', async () => {
  const w = mountInput()
  const store = useChatStore()
  store.visionSupported = true
  attState.items.value = [{ key: 'b', status: 'ready', id: 1, url: '/api/attachments/1' }]
  await w.find('textarea').setValue('看图')
  const send = vi.spyOn(store, 'send').mockReturnValue(true)
  await w.find('.zc-send-btn').trigger('click')
  expect(send).toHaveBeenCalledWith({
    text: '看图',
    attachments: [{ key: 'b', status: 'ready', id: 1, url: '/api/attachments/1' }],
  })
  expect(w.find('textarea').element.value).toBe('')
  expect(attState.clear).toHaveBeenCalled()

  await w.find('textarea').setValue('再看')
  send.mockReturnValue(false)
  await w.find('.zc-send-btn').trigger('click')
  expect(w.find('textarea').element.value).toBe('再看')
})

it('切换会话清空附件、保留文字', async () => {
  const w = mountInput()
  await w.find('textarea').setValue('保留我')
  useChatStore().currentSessionId = 9
  await nextTick()
  expect(attState.clear).toHaveBeenCalled()
  expect(w.find('textarea').element.value).toBe('保留我')
})
