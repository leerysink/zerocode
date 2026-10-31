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
import ToolCallBlock from './ToolCallBlock.vue'
import AskUserCard from './AskUserCard.vue'
import { applyEvent, useChatStore } from '../stores/chat'
import { DECLINE_ANSWER } from '../stores/messageModel'

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

  // 历史行里 args 不在 tool 行上，而在前一条 assistant 行的 tool_args.tool_calls 里（JSON 字符串）。
  // 归一化必须把它抽出来配到 tool 记录上，否则刷新后每个工具块都只剩「结果」、丢掉「参数」。
  it('历史行经归一化后，工具块同时拿到 args 与 result', () => {
    const w = mountMessages([
      {
        role: 'assistant', content: '让我看看',
        tool_args: {
          tool_calls: [{
            id: 'c1', type: 'function',
            function: { name: 'read_file', arguments: '{"path":"a.txt"}' },
          }],
        },
      },
      { role: 'tool', tool_call_id: 'c1', tool_name: 'read_file', tool_result: { ok: true } },
    ])
    expect(w.findComponent(ToolCallBlock).props('msg')).toEqual({
      role: 'tool', name: 'read_file', args: { path: 'a.txt' }, result: { ok: true },
    })
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

  it('重启确认卡：待答态由调用行上的标记驱动，标题与按钮换成重启口径，正文用 summary', () => {
    const w = mountMessages([
      {
        role: 'tool', name: 'preview_restart', args: { wait_seconds: 240 }, awaitingConfirm: true,
        confirmSummary: '应用「演示应用」的 dev 预览正在运行，重启会中断正在进行的调试。确定重启？',
      },
    ])
    expect(w.text()).toContain('重启预览')
    expect(w.text()).toContain('正在运行')
    expect(w.text()).toContain('确认重启')
    expect(w.find('.zc-confirm-args').exists()).toBe(false)   // 有 summary 就不甩 args JSON
    expect(w.find('.zc-preview').exists()).toBe(false)        // 待答时确认卡接管，不画「正在重启…」
  })

  it('写入确认卡文案一字不变（无 summary 时回落 args 摘要）', () => {
    const w = mountMessages([
      { role: 'tool', name: 'write_file', args: { path: 'a.txt', content: 'hi' }, awaitingConfirm: true },
    ])
    expect(w.text()).toContain('写入确认')
    expect(w.text()).toContain('确认写入')
    expect(w.find('.zc-confirm-args').text()).toContain('a.txt')
  })

  it('刷新后 hub 补发的标记能重建可交互的待答确认卡', () => {
    // 刷新/切会话回到会话时，store.messages 是历史行：待确认的那次调用还没有 tool 行
    // （工具行要等执行完才落库），卡片只能靠重连补发的 confirm_request 重建。
    setActivePinia(createPinia())
    const s = useChatStore()
    s.messages = [{
      role: 'assistant', content: '方案已确认',
      tool_args: {
        tool_calls: [{
          id: 'c1', type: 'function',
          function: { name: 'write_file', arguments: '{"path":"a.txt","content":"hi"}' },
        }],
      },
    }]
    applyEvent(s, { type: 'confirm_request', tool: 'write_file', args: { path: 'a.txt', content: 'hi' } })
    const w = mountMessages(s.messages)
    expect(w.text()).toContain('写入确认')
    expect(w.findAll('button')).toHaveLength(2)   // 确认写入 / 拒绝
    expect(w.find('.zc-confirm-args').text()).toContain('a.txt')
  })

  it('已答态确认卡由结果行的 confirm 字段推出，历史行同样能画', () => {
    const w = mountMessages([
      {
        role: 'assistant', content: '我来写',
        tool_args: {
          tool_calls: [{
            id: 'c1', type: 'function',
            function: { name: 'write_file', arguments: '{"path":"a.txt"}' },
          }],
        },
      },
      { role: 'tool', tool_call_id: 'c1', tool_name: 'write_file', tool_result: { ok: true, confirm: 'approved' } },
    ])
    expect(w.text()).toContain('已批准写入')
    expect(w.find('.zc-confirm-args').text()).toContain('a.txt')
    expect(w.findAll('button')).toHaveLength(0)   // 只读：没有按钮
  })

  it('拒绝与中断各留各的痕', () => {
    const denied = mountMessages([
      { role: 'tool', name: 'db_write', args: { statements: [] }, result: { ok: false, confirm: 'denied' } },
    ])
    expect(denied.text()).toContain('已拒绝')
    const cancelled = mountMessages([
      { role: 'tool', name: 'write_file', args: { path: 'a.txt' }, result: { cancelled: true, confirm: 'cancelled' } },
    ])
    expect(cancelled.text()).toContain('已中断')
  })

  it('中断补写的普通工具行没有 confirm 字段，不得画成确认卡', () => {
    const w = mountMessages([
      { role: 'tool', name: 'read_file', args: { path: 'a.txt' }, result: { cancelled: true } },
    ])
    expect(w.find('.zc-confirm').exists()).toBe(false)
    expect(w.findComponent(ToolCallBlock).exists()).toBe(true)
  })

  it('预览工具批准后仍画预览卡（结果卡已能表达重启结局，不再叠一张确认卡）', () => {
    const w = mountMessages([
      {
        role: 'tool', name: 'preview_restart',
        result: { ok: true, status: 'running', healthy: true, url: 'http://10.0.0.5:8213', confirm: 'approved' },
      },
    ])
    expect(w.find('.zc-preview').exists()).toBe(true)
    expect(w.find('.zc-confirm').exists()).toBe(false)
  })
})

// ---- preview_page 检查卡（规格 2026-10-31）----

describe('preview_page 在消息流里的渲染', () => {
  const result = {
    ok: true,
    app: { slug: 'demo', stack: 'vue3-fastapi' },
    status: 'running',
    url: 'http://localhost:8241/orders',
    title: '订单管理',
    text: '订单列表\n共 3 条',
    text_truncated: false,
    console: [{ level: 'error', text: 'Uncaught TypeError: x' }],
    page_errors: [],
    failed_requests: [],
    screenshot: { url: '/api/browser/shots/7/a.png', width: 1280, height: 800, bytes: 100 },
    vision: { supported: true, text: '页面整体正常，订单表格已渲染。' },
    redacted: 2,
    truncated: false,
    checks_left: 5,
  }

  it('preview_page 走 PageInspectCard，显示文本/截图/视觉诊断', () => {
    const w = mountMessages([{ role: 'tool', name: 'preview_page', result }])
    expect(w.find('.zc-page').exists()).toBe(true)
    expect(w.find('.zc-preview').exists()).toBe(false)
    expect(w.text()).toContain('页面已打开（发现异常）')
    expect(w.text()).toContain('订单列表')
    expect(w.text()).toContain('页面整体正常')
    expect(w.find('.zc-page-link').attributes('href')).toBe('http://localhost:8241/orders')
    expect(w.find('.zc-page-shot img').attributes('src')).toBe('/api/browser/shots/7/a.png')
  })

  it('预览未运行显示状态与可行动提示', () => {
    const w = mountMessages([{
      role: 'tool', name: 'preview_page',
      result: { ok: false, status: 'stopped', url: 'http://localhost:8241',
                error: '预览未运行：先用 preview_restart 启动' },
    }])
    expect(w.text()).toContain('预览未就绪')
    expect(w.text()).toContain('preview_restart')
    expect(w.find('.zc-page-link').exists()).toBe(false)
  })

  it('实时流里 call 行与 result 行只画一张检查卡', () => {
    const w = mountMessages([
      { role: 'tool', name: 'preview_page', args: { path: '/orders' } },
      { role: 'tool', name: 'preview_page', result },
    ])
    expect(w.findAll('.zc-page').length).toBe(1)
    expect(w.text()).not.toContain('正在打开预览页')
  })
})

// ---- 提问卡进消息流（spec 2026-10-30 用户故事 1/5）----
// 卡片由 ask_user 调用行驱动：无结果行 = 待答可交互。不再依赖挂起态字段，
// 所以刷新/切会话/断线重连后，待答的题从历史行原样重建。

// 已答态要验真实渲染（高亮/灰显/无输入无按钮），所以这个挂载口不 stub AskUserCard
function mountWithRealAsk(messages) {
  setActivePinia(createPinia())
  return mount(ChatMessages, {
    props: { messages, hasSession: true },
    global: {
      stubs: {
        'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
        MarkdownView: true, ToolCallBlock: true, ThinkingBlock: true, TodoCard: true,
      },
    },
  })
}

describe('提问卡在消息流里', () => {
  it('ask_user 调用行（无结果）渲染成待答提问卡，不再落回折叠块', () => {
    const w = mountMessages([
      { role: 'tool', name: 'ask_user', args: { questions: [{ question: '选哪个', options: [{ label: 'A' }] }] } },
    ])
    expect(w.findComponent(AskUserCard).props('questions'))
      .toEqual([{ question: '选哪个', options: [{ label: 'A' }] }])
    expect(w.findComponent(ToolCallBlock).exists()).toBe(false)
  })

  it('历史行形状（args 在前一条 assistant 行上）同样画出待答卡', () => {
    const w = mountMessages([
      {
        role: 'assistant', content: '让我先确认一件事',
        tool_args: {
          tool_calls: [{
            id: 'c1', type: 'function',
            function: { name: 'ask_user', arguments: '{"questions":[{"question":"选哪个","options":[{"label":"A"}]}]}' },
          }],
        },
      },
      { role: 'tool', tool_call_id: 'c1', tool_name: 'ask_user' },
    ])
    expect(w.findComponent(AskUserCard).props('questions'))
      .toEqual([{ question: '选哪个', options: [{ label: 'A' }] }])
  })

  it('刷新后重连补发的问题能重建待答提问卡（历史里没有未执行工具的 tool 行）', () => {
    setActivePinia(createPinia())
    const s = useChatStore()
    s.messages = [{
      role: 'assistant', content: '让我先确认一件事',
      tool_args: {
        tool_calls: [{
          id: 'c1', type: 'function',
          function: { name: 'ask_user', arguments: '{"questions":[{"question":"选哪个"}]}' },
        }],
      },
    }]
    applyEvent(s, { type: 'ask_user', questions: [{ question: '选哪个', options: [{ label: 'A' }] }] })
    const w = mountMessages(s.messages)
    expect(w.findComponent(AskUserCard).props('questions'))
      .toEqual([{ question: '选哪个', options: [{ label: 'A' }] }])
  })

  it('模型给的字符串选项也能画出来（调用行存的是原始 args，没经后端规整）', () => {
    const w = mountMessages([
      { role: 'tool', name: 'ask_user', args: { question: '选哪个', options: ['A', 'B'] } },
    ])
    expect(w.findComponent(AskUserCard).props('questions'))
      .toEqual([{ question: '选哪个', options: [{ label: 'A' }, { label: 'B' }] }])
  })

  it('结果行到达后不再落回折叠 JSON 块，而是转成只读卡', () => {
    const w = mountMessages([
      { role: 'tool', name: 'ask_user', args: { questions: [{ question: '选哪个' }] }, result: { answers: [{ options: ['A'] }] } },
    ])
    expect(w.findComponent(AskUserCard).exists()).toBe(true)
    expect(w.findComponent(ToolCallBlock).exists()).toBe(false)
  })

  it('同一次执行里两次提问，两张卡各自独立按序渲染', () => {
    const w = mountMessages([
      { role: 'tool', name: 'ask_user', args: { questions: [{ question: '第一题' }] }, result: { answers: [{ options: ['A'] }] } },
      { role: 'tool', name: 'ask_user', args: { questions: [{ question: '第二题' }] } },
    ])
    const cards = w.findAllComponents(AskUserCard)
    expect(cards).toHaveLength(2)
    expect(cards[0].props('questions')).toEqual([{ question: '第一题' }])
    expect(cards[1].props('questions')).toEqual([{ question: '第二题' }])
  })

  it('已答态：选中项高亮带对勾、未选项灰显、输入框与按钮全部摘除', () => {
    const w = mountWithRealAsk([
      {
        role: 'tool', name: 'ask_user',
        args: { questions: [{ question: '选哪个', options: [{ label: '方案A' }, { label: '方案B' }] }] },
        result: { answers: [{ options: ['方案B'] }] },
      },
    ])
    expect(w.find('.zc-ask').classes()).toContain('is-answered')
    expect(w.find('.zc-ask-state').text()).toBe('已回答')
    expect(w.find('.zc-ask-question').text()).toBe('选哪个')
    const opts = w.findAll('.zc-opt')
    expect(opts).toHaveLength(2)
    expect(opts[0].classes()).toContain('is-dim')
    expect(opts[1].classes()).toContain('is-picked')
    expect(opts[1].text()).toContain('✓')
    // 只读：没有输入框，也没有任何按钮
    expect(w.find('textarea').exists()).toBe(false)
    expect(w.findAll('button')).toHaveLength(0)
  })

  it('已答态：手输回答原文附在选项区下方，未选中的选项仍灰显留痕', () => {
    const w = mountWithRealAsk([
      {
        role: 'tool', name: 'ask_user',
        args: { questions: [{ question: '叫什么名字', options: [{ label: '小蓝' }] }] },
        result: { answers: [{ answer: '就叫阿青' }] },
      },
    ])
    expect(w.find('.zc-ask-free-answer').text()).toBe('就叫阿青')
    expect(w.findAll('.zc-opt')[0].classes()).toContain('is-dim')
  })

  it('已答态：多选题同时高亮多个选中项', () => {
    const w = mountWithRealAsk([
      {
        role: 'tool', name: 'ask_user',
        args: { questions: [{ question: '要哪些', multi_select: true, options: [{ label: 'A' }, { label: 'B' }, { label: 'C' }] }] },
        result: { answers: [{ options: ['A', 'C'] }] },
      },
    ])
    expect(w.findAll('.zc-opt').map(o => o.classes().includes('is-picked'))).toEqual([true, false, true])
  })

  it('已中断态：显示「已中断」与原因，全选项灰显且不可交互', () => {
    const w = mountWithRealAsk([
      {
        role: 'tool', name: 'ask_user',
        args: { questions: [{ question: '选哪个', options: [{ label: 'A' }] }] },
        result: { cancelled: true, reason: '提问超时' },
      },
    ])
    expect(w.find('.zc-ask-state').text()).toContain('已中断')
    expect(w.find('.zc-ask-state').text()).toContain('提问超时')
    expect(w.findAll('.zc-opt.is-picked')).toHaveLength(0)
    expect(w.find('textarea').exists()).toBe(false)
    expect(w.findAll('button')).toHaveLength(0)
  })

  it('「自行决定」的已答态单独标注，与普通手输回答区分', () => {
    const w = mountWithRealAsk([
      {
        role: 'tool', name: 'ask_user',
        args: { questions: [{ question: '选哪个', options: [{ label: 'A' }] }] },
        result: { answers: [{ answer: DECLINE_ANSWER }] },
      },
    ])
    expect(w.find('.zc-ask-state').text()).toBe('自行决定')
    expect(w.find('textarea').exists()).toBe(false)
    expect(w.findAll('button')).toHaveLength(0)
  })

  // 同构不变式的组件层形态：同一次问答，实时形状与历史形状必须渲染出逐字相同的卡片
  it('历史行形状的已答卡与实时形状渲染逐字相同', () => {
    const live = mountWithRealAsk([
      {
        role: 'tool', name: 'ask_user',
        args: { questions: [{ question: '选哪个', options: [{ label: 'A' }, { label: 'B' }] }] },
        result: { answers: [{ options: ['B'] }] },
      },
    ])
    const hist = mountWithRealAsk([
      {
        role: 'assistant', content: '先确认一下',
        tool_args: {
          tool_calls: [{
            id: 'c1', type: 'function',
            function: { name: 'ask_user', arguments: '{"questions":[{"question":"选哪个","options":[{"label":"A"},{"label":"B"}]}]}' },
          }],
        },
      },
      { role: 'tool', tool_call_id: 'c1', tool_name: 'ask_user', tool_result: { answers: [{ options: ['B'] }] } },
    ])
    expect(hist.find('.zc-ask').html()).toBe(live.find('.zc-ask').html())
  })
})
