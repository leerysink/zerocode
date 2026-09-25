// @vitest-environment happy-dom
// SideBar 行为契约（两块独立需求，共用一套挂载桩）：
//   ① 会话组折叠回归：展开其余 N 个后必须能收起（原缺陷：expand 单向置位，只能刷新页面）。
//   ② 侧栏分组 = 应用：分组行由 GET /apps 驱动（不再显示手动建的旧工作区），行最右侧有
//      「+」（在该应用下直接建会话并打开）与「⋯」（重命名 / 删除应用）；删除属危险操作，
//      必须输入 slug 才放行（规格 08 FR8-08）。
// mock 掉 api 层与 antd message，专注组件自身的渲染与交互契约。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  // SideBar 直接使用
  listSessions: vi.fn(), deleteSession: vi.fn(), createSession: vi.fn(),
  listApps: vi.fn(), renameApp: vi.fn(), deleteApp: vi.fn(),
  // 账户区（Ticket 010）：昵称回显与登出
  authMe: vi.fn(), authLogout: vi.fn(),
  // chat store 使用（loadAll 仍要拉工作区名给文件面板/输入区做名称回显）
  listWorkspaces: vi.fn(), getMessages: vi.fn(), getSessionDetail: vi.fn(),
  listModels: vi.fn(), patchSessionModel: vi.fn(),
  wsUrl: vi.fn(() => 'ws://test'),
}))
vi.mock('ant-design-vue', () => ({ message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

import { listApps, listSessions, listWorkspaces, createSession, renameApp, deleteApp, authMe, authLogout } from '../api'
import { message } from 'ant-design-vue'
import SideBar from './SideBar.vue'
import { useChatStore } from '../stores/chat'

// 只渲染默认插槽的桩：折叠断言依赖 .zc-session / .zc-more 等组件自有元素
const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})
const ButtonStub = {
  name: 'a-button',
  render() {
    return h('button', { class: 'stub-btn', onClick: (e) => this.$emit('click', e) },
      this.$slots.default ? this.$slots.default() : [])
  },
}
// 下拉：真实实现里 overlay 是懒渲染的，桩把两块都渲染出来，好让菜单项可点
const DropdownStub = {
  name: 'a-dropdown',
  render() {
    return h('div', { class: 'stub-dropdown' },
      [...(this.$slots.default ? this.$slots.default() : []),
        ...(this.$slots.overlay ? this.$slots.overlay() : [])])
  },
}
// 弹窗：open=false 时不渲染（与 antd 一致，避免两个模态框在 DOM 里打架）；
// OK 按钮带上 okButtonProps.disabled，用于断言「没输 slug 不放行」这道闸门。
const ModalStub = {
  name: 'a-modal',
  props: ['open', 'okButtonProps', 'okText'],
  render() {
    if (!this.open) return null
    return h('div', { class: 'stub-modal' }, [
      ...(this.$slots.default ? this.$slots.default() : []),
      h('button', {
        class: 'stub-modal-ok',
        disabled: !!(this.okButtonProps && this.okButtonProps.disabled),
        onClick: () => this.$emit('ok'),
      }, 'ok'),
    ])
  },
}
// v-model:value 通路桩：点选项即回写，等价于用户在下拉里选中
const SelectStub = {
  name: 'a-select',
  props: ['value', 'options'],
  render() {
    return h('div', { class: 'stub-select' }, (this.options || []).map((o) =>
      h('button', { class: 'stub-option', key: o.value, onClick: () => this.$emit('update:value', o.value) },
        o.label)))
  },
}
const InputStub = {
  name: 'a-input',
  props: ['value'],
  render() {
    return h('input', {
      class: 'stub-input',
      value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value),
    })
  },
}
const STUBS = {
  'a-button': ButtonStub,
  'a-spin': slotDiv('a-spin'),
  'a-empty': slotDiv('a-empty'),
  'a-popconfirm': slotDiv('a-popconfirm'),
  'a-dropdown': DropdownStub,
  'a-modal': ModalStub,
  'a-select': SelectStub,
  'a-input': InputStub,
  'a-divider': slotDiv('a-divider'),
  'router-link': { name: 'router-link', props: ['to'], render() { return h('a', this.$slots.default?.()) } },
  NewSessionModal: true,
}

const APP = {
  id: 7, workspace_id: 1, name: '来料登记', slug: 'lailiao', status_dev: 'stopped',
  root_path: 'D:/project/zerocode/apps/lailiao',   // list_apps 带出的工作区目录，确认文案要用
}
const LEGACY_WS = { id: 99, name: 'zerocode', root_path: 'D:/project/zerocode' }

// 造 n 个会话，updated_at 递减保证按后端排序语义排列
function sessions(n, workspaceId = 1) {
  return Array.from({ length: n }, (_, i) => ({
    id: 100 + i,
    workspace_id: workspaceId,
    title: `会话${i + 1}`,
    status: 'idle',
    created_at: '2026-09-17T08:00:00Z',
    updated_at: `2026-09-17T1${i}:00:00Z`,
  }))
}

async function mountSideBar({ appRows = [APP], wsRows = [LEGACY_WS], sessionRows } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  listApps.mockResolvedValue({ data: appRows })
  listWorkspaces.mockResolvedValue({ data: wsRows })
  listSessions.mockImplementation(async (wsId) => ({ data: sessionRows ?? sessions(8, wsId) }))
  authMe.mockResolvedValue({ data: { id: 1, nickname: '小明' } })   // 账户区默认已登录
  const wrapper = mount(SideBar, { global: { plugins: [pinia], stubs: STUBS } })
  await flushPromises()
  const store = useChatStore()
  store.openSession = vi.fn()      // 打开会话要连 WS，与本组断言无关，桩掉
  return { wrapper, store }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('SideBar 会话折叠', () => {
  it('默认每组只显示 5 条，并出现「展开其余 3 个会话」', async () => {
    const { wrapper } = await mountSideBar()
    expect(wrapper.findAll('.zc-session')).toHaveLength(5)
    expect(wrapper.find('.zc-more').text()).toContain('展开其余 3 个会话')
  })

  it('点击「展开其余」后显示全部会话', async () => {
    const { wrapper } = await mountSideBar()
    await wrapper.find('.zc-more').trigger('click')
    expect(wrapper.findAll('.zc-session')).toHaveLength(8)
  })

  it('展开后出现「收起」入口，点击后恢复默认 5 条且展开链接重现', async () => {
    const { wrapper } = await mountSideBar()
    await wrapper.find('.zc-more').trigger('click')
    expect(wrapper.findAll('.zc-session')).toHaveLength(8)

    // 缺陷修复点：展开后必须有收起入口
    const collapse = wrapper.find('.zc-more')
    expect(collapse.exists()).toBe(true)
    expect(collapse.text()).toContain('收起')
    await collapse.trigger('click')

    expect(wrapper.findAll('.zc-session')).toHaveLength(5)
    expect(wrapper.find('.zc-more').text()).toContain('展开其余 3 个会话')
  })

  it('会话数不超过 5 条时不显示折叠链接', async () => {
    const { wrapper } = await mountSideBar({ sessionRows: sessions(4) })
    expect(wrapper.findAll('.zc-session')).toHaveLength(4)
    expect(wrapper.find('.zc-more').exists()).toBe(false)
  })
})

describe('SideBar 分组只显示应用', () => {
  it('分组行由 /api/apps 驱动，手动建的旧工作区不再出现', async () => {
    const { wrapper } = await mountSideBar({
      appRows: [APP, { ...APP, id: 8, workspace_id: 2, name: '进销存', slug: 'jxc' }],
    })
    const heads = wrapper.findAll('.zc-group-head')
    expect(heads).toHaveLength(2)
    const text = wrapper.find('.zc-groups').text()
    expect(text).toContain('来料登记')
    expect(text).toContain('进销存')
    expect(text).not.toContain('zerocode')            // LEGACY_WS 的名字不应出现在侧栏
  })

  it('会话按应用关联的工作区拉取', async () => {
    await mountSideBar({ appRows: [{ ...APP, workspace_id: 42 }] })
    expect(listSessions).toHaveBeenCalledWith(42)
  })

  it('没有应用时给出去「应用」页新建的引导', async () => {
    const { wrapper } = await mountSideBar({ appRows: [] })
    expect(wrapper.find('.zc-groups').text()).toContain('应用')
  })
})

describe('SideBar 分组行操作：+ 与 ⋯', () => {
  it('每组行尾都有 + 与 ⋯ 两个入口', async () => {
    const { wrapper } = await mountSideBar()
    expect(wrapper.find('.zc-group-head .zc-add').exists()).toBe(true)
    expect(wrapper.find('.zc-group-head .zc-menu-btn').exists()).toBe(true)
  })

  it('点 + 直接在该应用的工作区下建会话并打开，不弹任何窗', async () => {
    const { wrapper, store } = await mountSideBar()
    createSession.mockResolvedValue({ data: { id: 555, workspace_id: 1, title: '新会话' } })
    await wrapper.find('.zc-add').trigger('click')
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith({ workspace_id: 1 })
    expect(store.openSession).toHaveBeenCalledWith(555, 1)
  })

  it('+ 建会话失败时给出中文错误，不静默', async () => {
    const { wrapper, store } = await mountSideBar()
    createSession.mockRejectedValue({ response: { data: { detail: '工作区不存在' } } })
    await wrapper.find('.zc-add').trigger('click')
    await flushPromises()
    expect(message.error).toHaveBeenCalledWith('工作区不存在')
    expect(store.openSession).not.toHaveBeenCalled()
  })

  it('⋯ 菜单提供重命名与删除应用两项', async () => {
    const { wrapper } = await mountSideBar()
    expect(wrapper.find('.zc-menu-rename').exists()).toBe(true)
    expect(wrapper.find('.zc-menu-del').exists()).toBe(true)
  })
})

describe('SideBar 应用重命名', () => {
  it('菜单里点「重命名」弹出模态框，输入框预填当前应用名', async () => {
    const { wrapper } = await mountSideBar()
    await wrapper.find('.zc-menu-rename').trigger('click')
    expect(wrapper.find('.stub-modal').exists()).toBe(true)
    expect(wrapper.find('.stub-input').element.value).toBe('来料登记')
  })

  it('改名为空时不放行确认按钮', async () => {
    const { wrapper } = await mountSideBar()
    await wrapper.find('.zc-menu-rename').trigger('click')
    await wrapper.find('.stub-input').setValue('   ')
    expect(wrapper.find('.stub-modal-ok').attributes('disabled')).toBeDefined()
  })

  it('确认后调用改名接口并刷新列表，同时回灌工作区名（面包屑读的是 workspace.name）', async () => {
    const { wrapper } = await mountSideBar()
    renameApp.mockResolvedValue({ data: { ...APP, name: '新名字' } })
    await wrapper.find('.zc-menu-rename').trigger('click')
    await wrapper.find('.stub-input').setValue('新名字')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(renameApp).toHaveBeenCalledWith(7, '新名字')
    expect(message.success).toHaveBeenCalled()
    expect(listApps.mock.calls.length).toBeGreaterThan(1)   // 重新拉取应用列表
    expect(listWorkspaces.mock.calls.length).toBeGreaterThan(1)
  })
})

describe('SideBar 删除应用', () => {
  it('删除确认必须输入 slug 才放行', async () => {
    const { wrapper } = await mountSideBar()
    await wrapper.find('.zc-menu-del').trigger('click')
    expect(wrapper.find('.stub-modal').exists()).toBe(true)
    await wrapper.find('.stub-input').setValue('laili')          // 半个 slug：不够
    expect(wrapper.find('.stub-modal-ok').attributes('disabled')).toBeDefined()
    await wrapper.find('.stub-input').setValue('lailiao')        // 完整 slug：放行
    expect(wrapper.find('.stub-modal-ok').attributes('disabled')).toBeUndefined()
  })

  it('确认后只删平台记录：调用删除接口并刷新，磁盘工程与库保留', async () => {
    const { wrapper } = await mountSideBar()
    deleteApp.mockResolvedValue({ data: { ok: true, kept: { root_path: 'D:/apps/lailiao', slug: 'lailiao', databases: ['lailiao_dev', 'lailiao_prod'] } } })
    await wrapper.find('.zc-menu-del').trigger('click')
    await wrapper.find('.stub-input').setValue('lailiao')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(deleteApp).toHaveBeenCalledWith(7)
    expect(message.success).toHaveBeenCalled()
    expect(listApps.mock.calls.length).toBeGreaterThan(1)
  })

  it('确认文案里写清工程目录还在（用户能自救的路径）', async () => {
    const { wrapper } = await mountSideBar()
    await wrapper.find('.zc-menu-del').trigger('click')
    expect(wrapper.find('.stub-modal').text()).toContain('D:/project/zerocode/apps/lailiao')
  })

  it('删除当前正在聊的会话所属应用时，清空选中态', async () => {
    const { wrapper, store } = await mountSideBar()
    store.currentSessionId = 100            // 该应用下第一条会话
    store.currentWorkspaceId = 1
    deleteApp.mockResolvedValue({ data: { ok: true, kept: {} } })
    await wrapper.find('.zc-menu-del').trigger('click')
    await wrapper.find('.stub-input').setValue('lailiao')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(store.currentSessionId).toBeNull()
    expect(store.currentWorkspaceId).toBeNull()
  })
})

// Ticket 010 / spec 故事13：侧栏账户区——当前账号昵称回显 + 登出入口。
describe('SideBar 账户区', () => {
  it('页脚显示当前账号昵称（authMe）', async () => {
    const { wrapper } = await mountSideBar()
    expect(authMe).toHaveBeenCalled()
    expect(wrapper.find('[data-test="account-nickname"]').text()).toContain('小明')
  })

  it('点登出：调接口后整页跳登录页（登出后 cookie 已作废，留在原地只会处处 401）', async () => {
    const assign = vi.fn()
    const saved = window.location
    Object.defineProperty(window, 'location', { value: { assign }, writable: true, configurable: true })
    try {
      authLogout.mockResolvedValue({})
      const { wrapper } = await mountSideBar()
      await wrapper.find('[data-test="logout-btn"]').trigger('click')
      await flushPromises()
      expect(authLogout).toHaveBeenCalled()
      expect(assign).toHaveBeenCalledWith('/login')
    } finally {
      Object.defineProperty(window, 'location', { value: saved, writable: true, configurable: true })
    }
  })
})
