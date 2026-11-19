// @vitest-environment happy-dom
// 聊天页侧栏接线：左右手柄的渲染门、收起/展开、宽度恢复、双击复位与拖拽透传。
// 三栏子组件与 api 全部桩掉，只钉 ChatView 自己的装配行为。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { h, nextTick } from 'vue'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  listWorkspaces: vi.fn().mockResolvedValue({ data: [] }),
  getMessages: vi.fn(), getSessionDetail: vi.fn(), listModels: vi.fn(),
  patchSessionModel: vi.fn(), fetchAttachmentBlob: vi.fn(), uploadAttachment: vi.fn(),
  wsUrl: vi.fn(() => 'ws://test'),
}))

// 视图只调 message.warning 提示撤销；整模块桩掉（本文件模板组件走 STUBS，不依赖真 antd）
vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../components/SideBar.vue', async () => {
  const { h } = await import('vue')
  return {
    default: {
      name: 'SideBar',
      props: ['width', 'collapsed'],
      render() {
        return h('aside', {
          class: 'stub-sidebar',
          'data-width': String(this.width),
          'data-collapsed': String(this.collapsed),
        })
      },
    },
  }
})

vi.mock('../components/NavRail.vue', async () => {
  const { h } = await import('vue')
  return {
    NAV_RAIL_WIDTH: 64,
    default: { name: 'NavRail', render: () => h('nav', { class: 'stub-rail' }) },
  }
})

vi.mock('../components/FilePanel.vue', async () => {
  const { h } = await import('vue')
  return {
    default: {
      name: 'FilePanel',
      props: ['workspaceId', 'width', 'collapsed', 'readOnly'],
      render() {
        return h('aside', {
          class: 'stub-file-panel',
          'data-workspace': String(this.workspaceId),
          'data-width': String(this.width),
          'data-collapsed': String(this.collapsed),
          'data-read-only': String(this.readOnly),
        })
      },
    },
  }
})

vi.mock('../components/ChatMessages.vue', async () => {
  const { h } = await import('vue')
  return { default: { name: 'ChatMessages', render: () => h('div', { class: 'stub-messages' }) } }
})
vi.mock('../components/ChatInput.vue', async () => {
  const { h } = await import('vue')
  return { default: { name: 'ChatInput', render: () => h('div', { class: 'stub-input' }) } }
})
vi.mock('../components/TodoBar.vue', async () => {
  const { h } = await import('vue')
  return { default: { name: 'TodoBar', render: () => h('div', { class: 'stub-todos' }) } }
})

import ChatView from './ChatView.vue'
import { useChatStore } from '../stores/chat'
import { getMessages, getSessionDetail } from '../api'
import { PANELS_STORAGE_KEY } from '../composables/useChatSidePanels'

enableAutoUnmount(afterEach)

const STUBS = {
  'a-layout': {
    name: 'a-layout',
    render() {
      return h('div', { class: 'stub-layout' }, this.$slots.default ? this.$slots.default() : [])
    },
  },
}

async function mountView({ workspaceId = null, saved } = {}) {
  localStorage.clear()
  if (saved) localStorage.setItem(PANELS_STORAGE_KEY, JSON.stringify(saved))
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useChatStore()
  store.currentWorkspaceId = workspaceId
  const wrapper = mount(ChatView, { global: { plugins: [pinia], stubs: STUBS } })
  await flushPromises()
  return { wrapper, store }
}

const dumpPanels = () => JSON.parse(localStorage.getItem(PANELS_STORAGE_KEY) || 'null')

beforeEach(() => {
  Object.defineProperty(window, 'innerWidth', { value: 1600, configurable: true })
})

afterEach(() => {
  localStorage.clear()
})

describe('ChatView 侧栏手柄', () => {
  it('没有工作区：只有左手柄，不渲染文件面板与右手柄', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.find('.zc-edge-left .zc-edge-toggle').attributes('aria-label')).toBe('收起左侧栏')
    expect(wrapper.find('.zc-edge-right').exists()).toBe(false)
    expect(wrapper.find('.stub-file-panel').exists()).toBe(false)
    expect(wrapper.find('.stub-sidebar').attributes('data-width')).toBe('260')
  })

  it('窄栏常驻：排在左栏之前，左栏收起后仍在', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.find('.stub-layout').element.children[0].classList.contains('stub-rail')).toBe(true)
    await wrapper.find('.zc-edge-left .zc-edge-toggle').trigger('click')
    expect(wrapper.find('.stub-sidebar').attributes('data-collapsed')).toBe('true')
    expect(wrapper.find('.stub-rail').exists()).toBe(true)
  })

  it('点左钮：SideBar 收起、按钮变展开、拖拽热区消失并写入本地记忆', async () => {
    const { wrapper } = await mountView()

    await wrapper.find('.zc-edge-left .zc-edge-toggle').trigger('click')

    expect(wrapper.find('.stub-sidebar').attributes('data-collapsed')).toBe('true')
    expect(wrapper.find('.zc-edge-left .zc-edge-toggle').attributes('aria-label')).toBe('展开左侧栏')
    expect(wrapper.find('.zc-edge-left .zc-edge-drag').exists()).toBe(false)
    expect(dumpPanels().leftCollapsed).toBe(true)
  })

  it('有工作区：右栏按记忆宽度渲染，点右钮收起并写入本地记忆', async () => {
    const { wrapper } = await mountView({ workspaceId: 7 })

    const panel = wrapper.find('.stub-file-panel')
    expect(panel.attributes('data-workspace')).toBe('7')
    expect(panel.attributes('data-width')).toBe('300')
    expect(panel.attributes('data-collapsed')).toBe('false')

    await wrapper.find('.zc-edge-right .zc-edge-toggle').trigger('click')

    expect(wrapper.find('.stub-file-panel').attributes('data-collapsed')).toBe('true')
    expect(wrapper.find('.zc-edge-right .zc-edge-toggle').attributes('aria-label')).toBe('展开右侧栏')
    expect(dumpPanels().rightCollapsed).toBe(true)
  })

  it('恢复记忆：宽度与收起状态原样交给两侧面板', async () => {
    const { wrapper } = await mountView({
      workspaceId: 7,
      saved: { leftWidth: 340, rightWidth: 420, leftCollapsed: false, rightCollapsed: true },
    })

    expect(wrapper.find('.stub-sidebar').attributes('data-width')).toBe('340')
    expect(wrapper.find('.stub-file-panel').attributes('data-width')).toBe('420')
    expect(wrapper.find('.stub-file-panel').attributes('data-collapsed')).toBe('true')
    expect(wrapper.find('.zc-edge-right .zc-edge-toggle').attributes('aria-label')).toBe('展开右侧栏')
  })

  it('双击左分隔线：宽度回到默认 260 并写回本地记忆', async () => {
    const { wrapper } = await mountView({ saved: { leftWidth: 400 } })

    await wrapper.find('.zc-edge-left .zc-edge-drag').trigger('dblclick')

    expect(wrapper.find('.stub-sidebar').attributes('data-width')).toBe('260')
    expect(dumpPanels().leftWidth).toBe(260)
  })

  it('拖拽左分隔线：宽度实时传给 SideBar，松手落盘', async () => {
    const { wrapper } = await mountView()

    // VTU 的 trigger 无法给 PointerEvent 注入 clientX/button（原型上是只读 getter），直接派发 MouseEvent
    wrapper.find('.zc-edge-left .zc-edge-drag').element
      .dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 300 }))
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 360 }))
    await nextTick()
    expect(wrapper.find('.stub-sidebar').attributes('data-width')).toBe('320')

    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 360 }))
    expect(dumpPanels().leftWidth).toBe(320)
  })
})

describe('ChatView 共享只读（Ticket 007）', () => {
  class FakeWS {
    static opened = []
    constructor(url) { FakeWS.opened.push(url) }
    close() {}
    send() {}
  }
  let realWS
  beforeEach(() => {
    FakeWS.opened = []
    realWS = globalThis.WebSocket
    globalThis.WebSocket = FakeWS
    getMessages.mockResolvedValue({ data: [] })
    getSessionDetail.mockResolvedValue({
      data: { id: 1, workspace_id: 7, todo_state: [], effective_model: null },
    })
  })
  afterEach(() => { globalThis.WebSocket = realWS })

  it('被共享工作区：隐藏输入区、显示「共享旁观 · 只读」，右栏带只读标记', async () => {
    const { wrapper, store } = await mountView({ workspaceId: 7 })
    store.workspaces = [{ id: 7, name: '订单台账', role: 'collaborator' }]
    await nextTick()

    expect(wrapper.find('.stub-input').exists()).toBe(false)
    expect(wrapper.find('.zc-readonly-bar').text()).toContain('共享旁观')
    expect(wrapper.find('.zc-readonly-bar').text()).toContain('只读')
    expect(wrapper.find('.stub-file-panel').attributes('data-read-only')).toBe('true')
  })

  it('被共享工作区打开会话照常连 WS：后端按角色切旁观只读（Ticket 008）', async () => {
    const { store } = await mountView({ workspaceId: 7 })
    store.workspaces = [{ id: 7, name: '订单台账', role: 'collaborator' }]

    await store.openSession(1, 7)

    // 旁观也走同一个入口：连上后后端只下发事件、任何上行一律只读拒绝
    expect(FakeWS.opened).toEqual(['ws://test'])
    expect(store.currentSessionId).toBe(1)
  })

  it('所有者工作区不受影响：输入区在，打开会话照常连 WS', async () => {
    const { wrapper, store } = await mountView({ workspaceId: 7 })
    store.workspaces = [{ id: 7, name: '自有应用', role: 'owner' }]
    await nextTick()

    expect(wrapper.find('.stub-input').exists()).toBe(true)
    expect(wrapper.find('.zc-readonly-bar').exists()).toBe(false)
    expect(wrapper.find('.stub-file-panel').attributes('data-read-only')).toBe('false')

    await store.openSession(1, 7)
    expect(FakeWS.opened).toEqual(['ws://test'])
  })
})

// ---------- Ticket 010：撤销/退出旁观收口 ----------
// happy-dom 的 location.assign 不可直接改，按项目惯例整桩替换 + 还原（AccountBar.test 同款）
function stubLocation() {
  const assign = vi.fn()
  const saved = window.location
  Object.defineProperty(window, 'location', { value: { assign }, writable: true, configurable: true })
  return { assign, restore: () => Object.defineProperty(window, 'location', { value: saved, writable: true, configurable: true }) }
}

describe('ChatView 共享撤销', () => {
  it('收到 share_revoked（store 标记置位）：提示「共享已撤销」并整页回应用页', async () => {
    const { assign, restore } = stubLocation()
    const { message } = await import('ant-design-vue')
    const { store } = await mountView()
    vi.useFakeTimers()
    try {
      store.shareRevoked = true
      await nextTick()
      expect(message.warning).toHaveBeenCalledWith('共享已撤销，已退出旁观')
      expect(assign).not.toHaveBeenCalled()   // 先让人看清提示
      vi.advanceTimersByTime(600)
      expect(assign).toHaveBeenCalledWith('/apps')
    } finally {
      vi.useRealTimers()
      restore()
    }
  })
})
