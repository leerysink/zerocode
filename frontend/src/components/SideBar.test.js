// @vitest-environment happy-dom
// SideBar 行为契约（三块独立需求，共用一套挂载桩）：
//   ① 会话组折叠回归：展开其余 N 个后必须能收起（原缺陷：expand 单向置位，只能刷新页面）。
//   ② 侧栏分组 = 应用：分组行由 GET /apps 驱动（不再显示手动建的旧工作区），行最右侧有
//      「+」（在该应用下直接建会话并打开）与「⋯」（重命名 / 删除应用）；删除属危险操作，
//      必须输入 slug 才放行（规格 08 FR8-08）。
//   ③ 文件夹折叠与拖动排序的接线：箭头示状态、点组头折叠并跨挂载记忆、按存好的顺序渲染、
//      按住组头拖动能换位并落盘（顺序/折叠的算法本身见 useSidebarFolders.test.js）。
// mock 掉 api 层与 antd message，专注组件自身的渲染与交互契约。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { h, nextTick } from 'vue'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  // SideBar 直接使用
  listSessions: vi.fn(), deleteSession: vi.fn(), createSession: vi.fn(),
  listApps: vi.fn(), renameApp: vi.fn(), deleteApp: vi.fn(),
  // chat store 使用（loadAll 仍要拉工作区名给文件面板/输入区做名称回显）
  listWorkspaces: vi.fn(), getMessages: vi.fn(), getSessionDetail: vi.fn(),
  listModels: vi.fn(), patchSessionModel: vi.fn(),
  // 会话状态角标轮询（useSessionLiveStatus）
  getSessionLiveStatuses: vi.fn(),
  wsUrl: vi.fn(() => 'ws://test'),
}))
vi.mock('ant-design-vue', () => ({ message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

import {
  listApps, listSessions, listWorkspaces, createSession, renameApp, deleteApp, deleteSession,
  getSessionLiveStatuses,
} from '../api'
import { message } from 'ant-design-vue'
import SideBar from './SideBar.vue'
import { FOLDERS_STORAGE_KEY } from '../composables/useSidebarFolders'
import { useChatStore } from '../stores/chat'

// 只渲染默认插槽的桩：折叠断言依赖 .zc-session / .zc-more 等组件自有元素
const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})
// 气泡确认：真实实现的确认态是浮层，桩直接给一个「确认」按钮，好让删除链路可驱动
const PopconfirmStub = {
  name: 'a-popconfirm',
  render() {
    return h('div', { class: 'stub-popconfirm' }, [
      ...(this.$slots.default ? this.$slots.default() : []),
      h('button', { class: 'stub-popconfirm-ok', onClick: () => this.$emit('confirm') }, 'confirm'),
    ])
  },
}
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
  'a-popconfirm': PopconfirmStub,
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

async function mountSideBar({ appRows = [APP], wsRows = [LEGACY_WS], sessionRows, liveRows = [], props } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  listApps.mockResolvedValue({ data: appRows })
  listWorkspaces.mockResolvedValue({ data: wsRows })
  listSessions.mockImplementation(async (wsId) => ({ data: sessionRows ?? sessions(8, wsId) }))
  getSessionLiveStatuses.mockResolvedValue({ data: { sessions: liveRows } })
  const wrapper = mount(SideBar, { props, global: { plugins: [pinia], stubs: STUBS } })
  await flushPromises()
  const store = useChatStore()
  store.openSession = vi.fn()      // 打开会话要连 WS，与本组断言无关，桩掉
  return { wrapper, store }
}

// 状态轮询在共享 document 上挂 visibilitychange 监听并续 setTimeout：不自动卸载会串到下一条用例
enableAutoUnmount(afterEach)

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear() // 折叠/顺序现在会落本机记忆，不清会串到下一条用例
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

describe('SideBar 文件夹折叠与拖动排序', () => {
  const TWO = [APP, { ...APP, id: 8, workspace_id: 2, name: '进销存', slug: 'jxc' }]

  const headEls = (wrapper) => wrapper.findAll('.zc-group-head')
  const bodyEls = (wrapper) => wrapper.findAll('.zc-group-body')
  const arrowEls = (wrapper) => wrapper.findAll('.zc-fold-arrow')
  const names = (wrapper) => wrapper.findAll('.zc-ws-name').map((n) => n.text())
  const hidden = (body) => (body.attributes('style') || '').includes('display: none')
  const savedFolders = () => JSON.parse(localStorage.getItem(FOLDERS_STORAGE_KEY) || '{}')

  // 真实指针事件：VTU 的 trigger('pointerdown') 造出来的事件没有 button，会被 startDrag 挡掉
  const press = (el, clientY) => el.dispatchEvent(
    new MouseEvent('pointerdown', { clientY, button: 0, bubbles: true }))
  const dragTo = (clientY) => window.dispatchEvent(new MouseEvent('pointermove', { clientY }))
  const drop = () => window.dispatchEvent(new MouseEvent('pointerup'))

  it('默认全部展开：箭头带 is-open（朝下），成员看得见', async () => {
    const { wrapper } = await mountSideBar({ appRows: TWO })
    expect(arrowEls(wrapper).every((a) => a.classes('is-open'))).toBe(true)
    expect(bodyEls(wrapper).every((b) => !hidden(b))).toBe(true)
  })

  it('点组头折叠该组：成员隐藏、箭头转回朝右', async () => {
    const { wrapper } = await mountSideBar({ appRows: TWO })
    await headEls(wrapper)[0].trigger('click')
    expect(hidden(bodyEls(wrapper)[0])).toBe(true)
    expect(hidden(bodyEls(wrapper)[1])).toBe(false)
    expect(arrowEls(wrapper)[0].classes('is-open')).toBe(false)
    expect(savedFolders().folded).toEqual([7])
  })

  it('折叠状态跨挂载保留——刷新、切页回来都还在', async () => {
    const first = await mountSideBar({ appRows: TWO })
    await first.wrapper.find('.zc-group-head').trigger('click')
    first.wrapper.unmount()

    const { wrapper } = await mountSideBar({ appRows: TWO })
    expect(hidden(bodyEls(wrapper)[0])).toBe(true)
  })

  it('按存好的顺序渲染分组，新出现的应用顶到最前', async () => {
    localStorage.setItem(FOLDERS_STORAGE_KEY, JSON.stringify({ order: [8, 7], folded: [] }))
    const { wrapper } = await mountSideBar({ appRows: TWO })
    expect(names(wrapper)).toEqual(['进销存', '来料登记'])

    const { wrapper: withNew } = await mountSideBar({
      appRows: [{ ...APP, id: 9, workspace_id: 3, name: '新开的', slug: 'new' }, ...TWO],
    })
    expect(names(withNew)[0]).toBe('新开的')
  })

  it('按住组头上下拖动能换序并落盘', async () => {
    const { wrapper } = await mountSideBar({ appRows: TWO })
    expect(names(wrapper)).toEqual(['来料登记', '进销存'])
    press(headEls(wrapper)[0].element, 60)
    dragTo(600) // happy-dom 里所有行高都是 0，越过所有中线即落到最后一格
    drop()
    await nextTick()
    expect(names(wrapper)).toEqual(['进销存', '来料登记'])
    expect(savedFolders().order).toEqual([8, 7])
  })

  it('拖完浏览器补发的那次 click 不会顺手把该组折起来', async () => {
    const { wrapper } = await mountSideBar({ appRows: TWO })
    const head = headEls(wrapper)[0]
    press(head.element, 60)
    dragTo(600)
    drop()
    await head.trigger('click')
    expect(hidden(bodyEls(wrapper)[0])).toBe(false)
    expect(savedFolders().folded).toEqual([])
  })

  it('行尾的 + 不翻折叠：新建会话只把该组确保展开', async () => {
    const { wrapper } = await mountSideBar({ appRows: TWO })
    createSession.mockResolvedValue({ data: { id: 555, workspace_id: 1, title: '新会话' } })
    await wrapper.find('.zc-add').trigger('click')
    await flushPromises()
    expect(hidden(bodyEls(wrapper)[0])).toBe(false)
    expect(savedFolders().folded).toEqual([])
  })
})

// 账户区已迁至右上角顶栏 AccountBar，用例见 AccountBar.test.js（Ticket 010 后的位置调整）。

describe('SideBar 受控宽度', () => {
  it('展开态用传入宽度；收起态宽度归零、内容仍留在 DOM 里', async () => {
    const { wrapper } = await mountSideBar({ props: { width: 320 } })
    expect(wrapper.find('.zc-sidebar').element.style.width).toBe('320px')

    await wrapper.setProps({ collapsed: true })
    expect(wrapper.find('.zc-sidebar').element.style.width).toBe('0px')
    expect(wrapper.find('.zc-sidebar').classes()).toContain('is-collapsed')
    expect(wrapper.find('.zc-logo').exists()).toBe(true)
  })
})

// ---- 会话状态角标（spec 2026-11-04 US-9~16）----

const liveRow = (id, extra = {}) => ({
  session_id: id,
  running: false,
  waiting: 'none',
  last_outcome: null,
  finished_at: null,
  ...extra,
})
const finishedAfterPageOpen = () => Date.now() / 1000 + 60

describe('SideBar 会话状态角标', () => {
  it('运行中显示转圈，位置在标题之后、时间之前', async () => {
    const { wrapper } = await mountSideBar({ liveRows: [liveRow(100, { running: true })] })
    const row = wrapper.findAll('.zc-session')[0]
    const badge = row.find('.zc-session-status')
    expect(badge.exists()).toBe(true)
    expect(badge.classes()).toContain('is-running')
    expect(badge.attributes('title')).toBe('运行中')

    const kids = [...row.element.children]
    expect(kids[0].classList.contains('zc-title')).toBe(true)
    expect(kids[1].classList.contains('zc-session-status')).toBe(true)
    expect(kids[2].classList.contains('zc-time')).toBe(true)
  })

  it('等待操作优先于运行中：同一会话只显示黄色', async () => {
    const { wrapper } = await mountSideBar({
      liveRows: [liveRow(100, { running: true, waiting: 'ask' })],
    })
    const badge = wrapper.find('.zc-session-status')
    expect(badge.classes()).toContain('is-waiting')
    expect(badge.classes()).not.toContain('is-running')
    expect(badge.attributes('title')).toBe('等待你回答或确认')
  })

  it('页面打开后完成的显示绿色；点击后消除并打开会话', async () => {
    const { wrapper, store } = await mountSideBar({
      liveRows: [liveRow(100, { last_outcome: 'completed', finished_at: finishedAfterPageOpen() })],
    })
    const badge = wrapper.find('.zc-session-status')
    expect(badge.classes()).toContain('is-completed')
    expect(badge.attributes('title')).toBe('已完成')

    await badge.trigger('click')
    expect(store.openSession).toHaveBeenCalledTimes(1)
    expect(store.openSession).toHaveBeenCalledWith(100, 1)
    expect(wrapper.find('.zc-session-status').exists()).toBe(false)
  })

  it('失败与用户停止显示红色，点击同样消除', async () => {
    const { wrapper, store } = await mountSideBar({
      liveRows: [
        liveRow(100, { last_outcome: 'error', finished_at: finishedAfterPageOpen() }),
        liveRow(101, { last_outcome: 'cancelled', finished_at: finishedAfterPageOpen() }),
      ],
    })
    const badges = wrapper.findAll('.zc-session-status')
    expect(badges).toHaveLength(2)
    expect(badges[0].classes()).toContain('is-failed')
    expect(badges[0].attributes('title')).toBe('已停止或失败')
    expect(badges[1].classes()).toContain('is-failed')

    await badges[0].trigger('click')
    expect(wrapper.findAll('.zc-session-status')).toHaveLength(1)
    expect(store.openSession).toHaveBeenCalledWith(100, 1)
  })

  it('页面打开前就结束的执行不补提醒', async () => {
    const { wrapper } = await mountSideBar({
      liveRows: [liveRow(100, {
        last_outcome: 'completed',
        finished_at: Date.now() / 1000 - 60,
      })],
    })
    expect(wrapper.find('.zc-session-status').exists()).toBe(false)
  })

  it('当前正在看的会话也显示角标，点击运行态只打开不消除', async () => {
    const { wrapper, store } = await mountSideBar({ liveRows: [liveRow(100, { running: true })] })
    store.currentSessionId = 100
    await nextTick()
    expect(wrapper.findAll('.zc-session')[0].classes()).toContain('active')
    const badge = wrapper.find('.zc-session-status')
    expect(badge.exists()).toBe(true)

    await badge.trigger('click')
    expect(store.openSession).toHaveBeenCalledWith(100, 1)
    expect(wrapper.find('.zc-session-status').exists()).toBe(true)
  })

  it('没有实时事实的会话不显示角标', async () => {
    const { wrapper } = await mountSideBar({ liveRows: [liveRow(100)] })
    expect(wrapper.find('.zc-session-status').exists()).toBe(false)
  })

  it('刷新（重新挂载）后消除记录不保留，结束态重新出现', async () => {
    const row = liveRow(100, {
      last_outcome: 'completed',
      finished_at: finishedAfterPageOpen(),
    })
    const first = await mountSideBar({ liveRows: [row] })
    await first.wrapper.find('.zc-session-status').trigger('click')
    expect(first.wrapper.find('.zc-session-status').exists()).toBe(false)
    first.wrapper.unmount()

    const second = await mountSideBar({ liveRows: [row] })
    expect(second.wrapper.find('.zc-session-status').classes()).toContain('is-completed')
  })
})

describe('SideBar 删除会话', () => {
  it('删除会话后清掉它的切回缓存（下次打开不是删掉的内容）', async () => {
    const { wrapper, store } = await mountSideBar()
    const spy = vi.spyOn(store, 'forgetSession')
    deleteSession.mockResolvedValue({ data: { ok: true } })
    await wrapper.findAll('.stub-popconfirm-ok')[0].trigger('click')
    await flushPromises()
    expect(deleteSession).toHaveBeenCalledWith(100)
    expect(spy).toHaveBeenCalledWith(100)
  })
})
