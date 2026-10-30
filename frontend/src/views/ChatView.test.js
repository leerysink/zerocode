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

vi.mock('../components/FilePanel.vue', async () => {
  const { h } = await import('vue')
  return {
    default: {
      name: 'FilePanel',
      props: ['workspaceId', 'width', 'collapsed'],
      render() {
        return h('aside', {
          class: 'stub-file-panel',
          'data-workspace': String(this.workspaceId),
          'data-width': String(this.width),
          'data-collapsed': String(this.collapsed),
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
