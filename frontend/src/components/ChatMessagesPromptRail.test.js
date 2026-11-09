// @vitest-environment happy-dom
// 提问导航条接进 ChatMessages 的接缝：锚点、渲染门、点击定位。
// happy-dom 没有布局引擎，offsetTop/scrollHeight 恒为 0，所以这里给提问行桩出一套可预测的几何，
// 量到的几何才是真的——盒内定位本身由 promptRailModel.test.js 用纯数字覆盖，两边不重复。
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  getMessages: vi.fn(), getSessionDetail: vi.fn(), listModels: vi.fn(),
  listWorkspaces: vi.fn(), patchSessionModel: vi.fn(), wsUrl: vi.fn(() => 'ws://t'),
}))

import ChatMessages from './ChatMessages.vue'

// 提问行按 data-prompt-index 给出 top = 500 + 下标*1000；滚动区固定 4000/800
const LAYOUT_PROPS = {
  offsetTop: {
    configurable: true,
    get() {
      const i = this.getAttribute?.('data-prompt-index')
      return i === null ? 0 : 500 + Number(i) * 1000
    },
  },
  offsetHeight: { configurable: true, get() { return 60 } },
  // 滚动位置存在元素自己身上：测试里赋值即可驱动高亮重算，不必真滚
  scrollTop: {
    configurable: true,
    get() { return this.__fakeScrollTop ?? 0 },
    set(v) { this.__fakeScrollTop = v },
  },
  scrollHeight: { configurable: true, get() { return this.classList?.contains('zc-chat') ? 4000 : 0 } },
  clientHeight: { configurable: true, get() { return this.classList?.contains('zc-chat') ? 800 : 0 } },
}

beforeAll(() => {
  for (const [name, desc] of Object.entries(LAYOUT_PROPS)) {
    Object.defineProperty(HTMLElement.prototype, name, desc)
  }
})
afterAll(() => {
  for (const name of Object.keys(LAYOUT_PROPS)) {
    delete HTMLElement.prototype[name]
  }
})

// tooltip 桩：把触发区与 title 浮层都渲出来，否则浮层内容测不到
const TooltipStub = {
  name: 'a-tooltip',
  render() {
    return h('span', { class: 'stub-tooltip' }, [
      h('span', { class: 'stub-tip-title' }, this.$slots.title ? this.$slots.title() : []),
      ...(this.$slots.default ? this.$slots.default() : []),
    ])
  },
}

async function mountChat(messages, { hasSession = true } = {}) {
  setActivePinia(createPinia())
  const w = mount(ChatMessages, {
    props: { messages, hasSession },
    global: {
      stubs: {
        'a-tooltip': TooltipStub, 'a-image': true, 'a-image-preview-group': true,
        'a-button': true, 'a-spin': true,
        MarkdownView: true, ToolCallBlock: true, AskUserCard: true, ConfirmCard: true,
        ThinkingBlock: true, TodoCard: true, PreviewCard: true, PageInspectCard: true,
        BrowserFlowCard: true,
      },
    },
  })
  // 位置要挂载后才量得到，横杠比正文晚一帧出现
  await nextTick()
  return w
}

const TWO_PROMPTS = [
  { role: 'user', content: '第一问' },
  { role: 'assistant', content: '答一' },
  { role: 'user', content: '第二问' },
]

describe('锚点', () => {
  it('每条 user 气泡行都带 data-prompt-index，值是它在视图里的下标', async () => {
    const w = await mountChat(TWO_PROMPTS)
    expect(w.findAll('.zc-msg.is-user').map((n) => n.attributes('data-prompt-index'))).toEqual(['0', '2'])
  })

  it('没有文字也没有附件的提问照样挂锚点（它被轨道丢弃，但不能让下标错位）', async () => {
    const w = await mountChat([
      { role: 'user', content: '有内容' },
      { role: 'user', content: '' },
      { role: 'user', content: '也问了一句' },
    ])
    expect(w.findAll('.zc-msg.is-user').map((n) => n.attributes('data-prompt-index'))).toEqual(['0', '1', '2'])
    // 空提问不画横杠：轨道只剩两条
    expect(w.findAll('.zc-rail-mark').length).toBe(2)
  })
})

describe('渲染门与位置', () => {
  it('两条提问就画出两根横杠，按保留居中盒的像素位置成簇', async () => {
    const w = await mountChat(TWO_PROMPTS)
    const marks = w.findAll('.zc-rail-mark')
    expect(marks.length).toBe(2)
    // 盒高 200、间距 12：两条中心在 94 / 106，与提问在内容里的位置无关
    expect(marks.map((n) => n.attributes('style'))).toEqual(['top: 94px;', 'top: 106px;'])
  })

  it('只有一条提问时整条轨道不渲染', async () => {
    const w = await mountChat([{ role: 'user', content: '孤零零一问' }, { role: 'assistant', content: '答' }])
    expect(w.find('.zc-rail').exists()).toBe(false)
  })

  it('没有会话时不渲染轨道', async () => {
    // 空态下连提问行都不渲染，锚点无从量起
    const w = await mountChat(TWO_PROMPTS, { hasSession: false })
    expect(w.find('.zc-rail').exists()).toBe(false)
  })

  it('新提问进来后轨道跟着多一根', async () => {
    const w = await mountChat(TWO_PROMPTS)
    expect(w.findAll('.zc-rail-mark').length).toBe(2)
    await w.setProps({ messages: [...TWO_PROMPTS, { role: 'user', content: '第三问' }] })
    // 一帧给 DOM 打补丁，一帧给量测后的重渲染
    await nextTick()
    await nextTick()
    expect(w.findAll('.zc-rail-mark').length).toBe(3)
  })
})

describe('点击定位', () => {
  it('点横杠把滚动容器滚到那条提问的位置', async () => {
    const w = await mountChat(TWO_PROMPTS)
    const chat = w.find('.zc-chat').element
    const scrollTo = vi.fn()
    chat.scrollTo = scrollTo
    await w.findAll('.zc-rail-mark')[1].trigger('click')
    // 第二条 top 2500，留 12px 上边距
    expect(scrollTo).toHaveBeenCalledWith({ top: 2488, behavior: 'smooth' })
  })

  it('悬停浮层给出提问原文', async () => {
    const w = await mountChat(TWO_PROMPTS)
    expect(w.findAll('.stub-tip-title').map((n) => n.text())).toEqual(['第一问', '第二问'])
  })
})

describe('渐进渲染补齐', () => {
  it('首帧只画量得到的横杠，旧消息补齐后其余横杠出现', async () => {
    const many = Array.from({ length: 200 }, (_, i) => (
      i % 2 === 0 ? { role: 'user', content: `问${i}` } : { role: 'assistant', content: `答${i}` }
    ))
    const w = await mountChat(many)
    // 只铺最近 80 条：下标 120 起的提问才在 DOM 里，轨道也只画量得到的这 40 条
    expect(w.findAll('.zc-msg')).toHaveLength(80)
    expect(w.findAll('.zc-rail-mark')).toHaveLength(40)
    await vi.waitFor(() => {
      expect(w.findAll('.zc-msg')).toHaveLength(200)
      expect(w.findAll('.zc-rail-mark')).toHaveLength(100)
    })
  })
})

describe('滚动跟随', () => {
  it('滚动后高亮换到当前视口里的那条提问', async () => {
    const w = await mountChat(TWO_PROMPTS)
    // 停在顶部：第一条（top 500）在视口 [0,800] 里
    expect(w.findAll('.zc-rail-mark').map((n) => n.classes().includes('is-active'))).toEqual([true, false])
    const chat = w.find('.zc-chat')
    chat.element.scrollTop = 2000
    await chat.trigger('scroll')
    // 视口 [2000,2800]：只剩第二条（top 2500）在内
    expect(w.findAll('.zc-rail-mark').map((n) => n.classes().includes('is-active'))).toEqual([false, true])
  })
})
