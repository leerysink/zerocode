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
import { useChatStore } from '../stores/chat'

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

// ---- 预览工具结果卡与重启确认卡（规格 2026-10-29-agent-preview-tools §8）----

describe('预览工具在消息流里的渲染', () => {
  it('preview_* 结果走 PreviewCard，不再落回通用折叠块', () => {
    const w = mountMessages([
      { role: 'tool', name: 'preview_restart',
        result: { ok: true, status: 'running', healthy: true, url: 'http://10.0.0.5:8213' } },
    ])
    expect(w.find('.zc-preview').exists()).toBe(true)
    expect(w.text()).toContain('预览已就绪')
    expect(w.find('.zc-preview-link').attributes('href')).toBe('http://10.0.0.5:8213')
  })

  it('非预览工具仍走 ToolCallBlock', () => {
    const w = mountMessages([{ role: 'tool', name: 'read_file', result: { ok: true } }])
    expect(w.find('.zc-preview').exists()).toBe(false)
  })

  it('实时流里一次调用只画一张预览卡（call 行不再长驻「进行中」）', () => {
    // 后端 loop.py 把 tool_call 与 tool_result 分两次下发 → store 里是两条 message。
    // 闸门放过「有结果」与「最后一条（仍在进行）」，所以这里只有 result 行画卡；
    // 去掉闸门会变成两张：call 行永久的「正在重启预览…／等待平台步骤…」+ 刷新后只剩一张
    const w = mountMessages([
      { role: 'tool', name: 'preview_restart', args: { wait_seconds: 240 } },
      { role: 'tool', name: 'preview_restart',
        result: { ok: true, status: 'running', healthy: true, url: 'http://10.0.0.5:8213' } },
    ])
    expect(w.findAll('.zc-preview').length).toBe(1)
    expect(w.text()).not.toContain('正在重启预览')
    expect(w.text()).not.toContain('等待平台步骤')
    expect(w.text()).toContain('预览已就绪')
  })

  it('进行中的预览卡拿得到进度，且有结果后不再拿', () => {
    setActivePinia(createPinia())
    const store = useChatStore()
    store.toolProgress = { name: 'preview_restart', line: '[zerocode] → 起栈中' }
    const w = mount(ChatMessages, {
      props: {
        messages: [{ role: 'tool', name: 'preview_restart', args: { wait_seconds: 240 } }],
        hasSession: true,
      },
      global: {
        stubs: {
          'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
          MarkdownView: true, ToolCallBlock: true, AskUserCard: true,
          ThinkingBlock: true, TodoCard: true,
        },
      },
    })
    expect(w.text()).toContain('起栈中')
  })

  // liveProgress 的三道闸各钉一条：同名、无结果、最后一条。store.toolProgress 是单行、后写覆盖，
  // 并行跑两个预览工具时只剩最后一个名字——不按工具名匹配就会把别人那行挂到这张卡上。
  function mountWithProgress(messages, progress) {
    setActivePinia(createPinia())
    useChatStore().toolProgress = progress
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

  it('进度只挂在同名的预览卡上', () => {
    const w = mountWithProgress(
      [{ role: 'tool', name: 'preview_status', args: {} }],
      { name: 'preview_restart', line: '[zerocode] → 起栈中' },
    )
    expect(w.text()).toContain('正在读取预览状态')
    expect(w.text()).not.toContain('起栈中')
  })

  it('预览卡有结果后不再挂进度（终态收口，不留假在跑）', () => {
    const w = mountWithProgress(
      [{ role: 'tool', name: 'preview_restart',
         result: { ok: true, status: 'running', healthy: true, url: 'http://10.0.0.5:8213' } }],
      { name: 'preview_restart', line: '[zerocode] → 起栈中' },
    )
    expect(w.text()).toContain('预览已就绪')
    expect(w.text()).not.toContain('起栈中')
  })

  it('非最后一条、又没结果的 call 行不画预览卡（历史卡不复活进度）', () => {
    // I-1 的闸门让这一行落回通用折叠块——它与其它工具的 call 行同形，不再有「长驻进行中」的卡。
    // 有结果的行即便不是最后一条仍然画卡（终态要看得见），那条由上面的「有结果后不再挂进度」守着；
    // 于是 liveProgress 里的「最后一条」判断退化为纯防御：路由层已经保证不会画出不可能的形状
    const w = mountWithProgress(
      [{ role: 'tool', name: 'preview_restart', args: {} },
       { role: 'assistant', content: '已经起好了' }],
      { name: 'preview_restart', line: '[zerocode] → 起栈中' },
    )
    expect(w.findAll('.zc-preview').length).toBe(0)
    expect(w.text()).not.toContain('正在重启预览')
    expect(w.text()).not.toContain('起栈中')
  })

  it('历史消息（tool_name / tool_result，无 args）也能画出终态', () => {
    const w = mountMessages([
      { role: 'tool', tool_name: 'preview_logs',
        tool_result: { ok: true, status: 'error', lines: ['10:00:01 [平台] ✗ 迁移失败'],
                       truncated: false, note: '', redacted: 0 } },
    ])
    expect(w.find('.zc-preview').exists()).toBe(true)
    expect(w.text()).toContain('预览日志（1 行）')
  })

  it('重启确认卡：标题与按钮换成重启口径，正文用 summary', () => {
    const w = mount(ChatMessages, {
      props: {
        messages: [], hasSession: true,
        pendingConfirm: {
          tool: 'preview_restart', args: { wait_seconds: 240 },
          summary: '应用「演示应用」的 dev 预览正在运行，重启会中断正在进行的调试。确定重启？',
        },
      },
      global: {
        stubs: {
          'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
          MarkdownView: true, ToolCallBlock: true, AskUserCard: true,
          ThinkingBlock: true, TodoCard: true, PreviewCard: true,
        },
      },
    })
    expect(w.text()).toContain('重启预览')
    expect(w.text()).toContain('正在运行')
    expect(w.text()).toContain('确认重启')
    expect(w.find('.zc-confirm-args').exists()).toBe(false)   // 有 summary 就不甩 args JSON
  })

  it('写入确认卡文案一字不变（无 summary 时回落 args 摘要）', () => {
    const w = mount(ChatMessages, {
      props: {
        messages: [], hasSession: true,
        pendingConfirm: { tool: 'write_file', args: { path: 'a.txt', content: 'hi' } },
      },
      global: {
        stubs: {
          'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
          MarkdownView: true, ToolCallBlock: true, AskUserCard: true,
          ThinkingBlock: true, TodoCard: true, PreviewCard: true,
        },
      },
    })
    expect(w.text()).toContain('写入确认')
    expect(w.text()).toContain('确认写入')
    expect(w.find('.zc-confirm-args').text()).toContain('a.txt')
  })
})
