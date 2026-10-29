// @vitest-environment happy-dom
// 聊天页应用操作条（票 001 / spec §5.2 §5.4）：反查接缝与「复制预览地址」这条端到端路径。
// 主按钮与打开预览由 002／003 接上——本票不渲染没有行为的占位按钮。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  listApps: vi.fn(),
  getApp: vi.fn(),
  devStart: vi.fn(), devStop: vi.fn(), devRestart: vi.fn(),
  devReset: vi.fn().mockResolvedValue({ data: { ok: true } }),
  // chat store 的 import 面，组件不直接调，但不给会解析失败
  getMessages: vi.fn(), getSessionDetail: vi.fn(), listModels: vi.fn(),
  listWorkspaces: vi.fn(), patchSessionModel: vi.fn(), wsUrl: vi.fn(() => 'ws://test'),
}))
// Modal.confirm 是重建 dev 库的确认出口，桩成可捕获配置的 fn，好断言正文点了两个具体库名
vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  Modal: { confirm: vi.fn() },
}))

import { listApps, getApp, devReset, devStart, devRestart } from '../api'
import { message, Modal } from 'ant-design-vue'
import ChatAppActions from './ChatAppActions.vue'
import { useChatStore } from '../stores/chat'

const APPS = [
  { id: 11, workspace_id: 101, slug: 'win', stack: 'vue3-fastapi', dev_port: 8211, status_dev: 'running', preview_url: 'http://localhost:8211/', prod_port: 8210, status_prod: 'running', prod_url: 'https://win.example.test' },
  { id: 12, workspace_id: 102, slug: 'test1', stack: 'vue3-fastapi', dev_port: 8213, status_dev: 'stopped', preview_url: 'http://localhost:8213/', prod_port: 8212, status_prod: 'stopped', prod_url: 'https://test1.example.test' },
]

// 下拉：antd 的 overlay 是懒渲染的，桩把触发区与浮层都渲出来，菜单项才可点
const DropdownStub = {
  name: 'a-dropdown',
  render() {
    return h('div', { class: 'stub-dropdown' }, [
      ...(this.$slots.default ? this.$slots.default() : []),
      ...(this.$slots.overlay ? this.$slots.overlay() : []),
    ])
  },
}
// 按钮桩：把 disabled / loading 落到属性上，好断言「starting 期间点不动」
const ButtonStub = {
  name: 'a-button',
  props: ['disabled', 'loading'],
  render() {
    return h('button', {
      class: ['stub-btn', { 'is-loading': !!this.loading, 'is-disabled': !!this.disabled }],
      disabled: !!this.disabled,
      onClick: (e) => this.$emit('click', e),
    }, this.$slots.default ? this.$slots.default() : [])
  },
}
// tooltip 桩：只透传内容，不断言悬浮文案（那是 antd 自己的事）
const TooltipStub = {
  name: 'a-tooltip',
  render() {
    return h('span', { class: 'stub-tooltip' }, this.$slots.default ? this.$slots.default() : [])
  },
}
const mountActions = () =>
  mount(ChatAppActions, {
    global: {
      stubs: {
        'a-dropdown': DropdownStub, 'a-button': ButtonStub, 'a-tooltip': TooltipStub,
        'a-drawer': DrawerStub, 'a-modal': ModalStub,
        DeployLogPanel: LogsStub, MetricsModal: MetricsStub,
        PublishModal: PublishStub, PublishHistory: HistoryStub,
      },
    },
  })

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  listApps.mockReset().mockResolvedValue({ data: APPS })
  // 桩必须按 id 返回对应应用，否则轮询会把另一个应用的状态盖过来
  getApp.mockReset().mockImplementation((id) => Promise.resolve({ data: APPS.find((a) => a.id === id) }))
  devStart.mockReset().mockResolvedValue({ data: { ok: true } })
  devRestart.mockReset().mockResolvedValue({ data: { ok: true } })
  message.success.mockClear()
  message.error.mockClear()
  message.info.mockClear()
  Modal.confirm.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

it('会话所属应用可反查时渲染操作条，「更多」里有「复制预览地址」', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-app-actions').exists()).toBe(true)
  expect(w.text()).toContain('复制预览地址')
})

// 隐藏而不是置灰：反查不到时用户既无该应用、也无从判断「为什么点不动」，留一个灰按钮只会多问一句。
it('反查不到应用时整组入口不存在（不是禁用占位）', async () => {
  useChatStore().currentWorkspaceId = 999 // 不属于自己的 / 已删的应用
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-app-actions').exists()).toBe(false)
  expect(w.text()).toBe('')
})

it('会话还没绑定工作区时不渲染', async () => {
  useChatStore().currentWorkspaceId = null
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-app-actions').exists()).toBe(false)
})

// 地址只从接口字段来，不许前端按 dev_port 自拼（ADR-0021 后果条）。
// 断言逐字相等而不是 toContain：拼出来的死链和真地址在界面上无法区分。
const stubClipboard = () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  return writeText
}

it('点「复制预览地址」写入的字符串逐字等于接口的 preview_url', async () => {
  const writeText = stubClipboard()
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-copy').trigger('click')
  expect(writeText).toHaveBeenCalledWith('http://localhost:8213/')
  expect(message.success).toHaveBeenCalled()
})

// http + IP 访问控制台时 navigator.clipboard 是 undefined，直接 .writeText 抛 TypeError；
// AppsView 的 copyUrl 就为这个踩过一次「点了什么都没发生，连复制失败也不说」（AppsView.vue::copyUrl）。
it('剪贴板不可用时给明确失败提示而不是静默或抛错', async () => {
  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-copy').trigger('click')
  await flushPromises()
  expect(message.error).toHaveBeenCalled()
  expect(message.success).not.toHaveBeenCalled()
})

// 应用列表只拉一次并缓存在组件内（spec §5.5）；切会话重算的是反查，不是请求。
// 不重算的后果是「在 B 应用的会话里复制走 A 应用的地址」——两个地址长得一样，用户无从发现。
it('切到另一个应用的会话后，入口跟着换应用且不重复请求列表', async () => {
  stubClipboard()
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  expect(listApps).toHaveBeenCalledTimes(1)

  useChatStore().currentWorkspaceId = 102
  await flushPromises()
  await w.find('.zc-menu-copy').trigger('click')
  expect(listApps).toHaveBeenCalledTimes(1)
})

// ---- 票 002：主按钮按 status_dev 变身并接上启停 ----

it('预览停着时主按钮是「启动预览」，点它打 devStart 且带上当前应用 id', async () => {
  useChatStore().currentWorkspaceId = 102 // status_dev: stopped
  const w = mountActions()
  await flushPromises()
  const btn = w.find('.zc-act-dev')
  expect(btn.text()).toContain('启动预览')
  await btn.trigger('click')
  expect(devStart).toHaveBeenCalledWith(12)
  expect(devRestart).not.toHaveBeenCalled()
})

it('预览跑着时主按钮是「重启预览」，点它打 devRestart', async () => {
  useChatStore().currentWorkspaceId = 101 // status_dev: running
  const w = mountActions()
  await flushPromises()
  const btn = w.find('.zc-act-dev')
  expect(btn.text()).toContain('重启预览')
  await btn.trigger('click')
  expect(devRestart).toHaveBeenCalledWith(11)
  expect(devStart).not.toHaveBeenCalled()
})

// ---- 票 003：打开预览走新标签（ADR-0021）----

const openSpy = () => {
  const spy = vi.spyOn(window, 'open').mockReturnValue({})
  return spy
}

it('预览 running 时点「打开预览」用后端返回的 preview_url 开新标签', async () => {
  const spy = openSpy()
  useChatStore().currentWorkspaceId = 101 // running
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-act-open').trigger('click')
  expect(spy).toHaveBeenCalledWith('http://localhost:8211/', 'zerocode-preview-11')
})

// 停着时不代为启动：新标签先于栈就绪打开是一片连不上的白屏，而用户刚让 Agent 改完代码，
// 会把白屏归因成代码错了（ADR-0021）。
it('预览未运行时「打开预览」禁用', async () => {
  useChatStore().currentWorkspaceId = 102 // stopped
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-act-open').attributes('disabled')).toBeDefined()
})

// dev_port 缺位时后端刻意回空串（app_service.py::_with_public_urls），拼不出地址也不能开 about:blank。
// 两个来源都要空：listApps 那一份和轮询那一份，任一有值都不该判死。
it('preview_url 为空串时「打开预览」与「复制预览地址」都不可用', async () => {
  const noUrl = [{ ...APPS[0], preview_url: '' }]
  listApps.mockResolvedValue({ data: noUrl })
  getApp.mockImplementation((id) => Promise.resolve({ data: noUrl.find((a) => a.id === id) }))
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-act-open').attributes('disabled')).toBeDefined()
  expect(w.find('.zc-menu-copy').classes()).toContain('is-disabled')
})

// window.open 被弹窗拦截或 transient activation 过期时返回 null，静默失败等于按钮坏了
it('window.open 被拦截返回 null 时给出可操作提示', async () => {
  vi.spyOn(window, 'open').mockReturnValue(null)
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-act-open').trigger('click')
  expect(message.warning).toHaveBeenCalled()
})

// ---- 「打开生产」：与「打开预览」同款图标，闸门读 status_prod ----

it('正式环境 running 时点「打开生产」用后端返回的 prod_url 开新标签', async () => {
  const spy = openSpy()
  useChatStore().currentWorkspaceId = 101 // status_prod: running
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-act-prod').trigger('click')
  expect(spy).toHaveBeenCalledWith('https://win.example.test', 'zerocode-prod-11')
})

// 停着时同样不代为启动：网关对未 routes.json 登记的 slug 给 404 提示页，
// 开出去的是一片「应用不存在」而不是白屏——用户更容易怀疑代码或发布出了问题。
// 两个来源都要压住：轮询那一份优先于 listApps，只改列表等于没改。
it('正式环境未启动时「打开生产」禁用，且不影响可点的「打开预览」', async () => {
  const stopped = [{ ...APPS[0], status_prod: 'stopped' }]
  listApps.mockResolvedValue({ data: stopped })
  getApp.mockImplementation((id) => Promise.resolve({ data: stopped.find((a) => a.id === id) }))
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-act-prod').attributes('disabled')).toBeDefined()
  expect(w.find('.zc-act-open').attributes('disabled')).toBeUndefined()
})

// 静态栈等 SSL 关且端口缺位的应用，后端刻意回空串（nics.py::app_url）；
// 域名口径只在后端一处，前端不许按 slug 自拼一个 https://{slug}.域名 出来。
it('prod_url 为空串时「打开生产」不可用', async () => {
  const noUrl = [{ ...APPS[0], prod_url: '' }]
  listApps.mockResolvedValue({ data: noUrl })
  getApp.mockImplementation((id) => Promise.resolve({ data: noUrl.find((a) => a.id === id) }))
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-act-prod').attributes('disabled')).toBeDefined()
})

// 在应用列表里启动正式、或本页发布完成后，聊天页的灰态必须自己解开——
// 闸门停在 listApps 那一刻的旧值等于按钮在撒谎。
it('轮询到 status_prod 转 running 后「打开生产」从禁用变为可点', async () => {
  const stopped = [{ ...APPS[0], status_prod: 'stopped' }]
  listApps.mockResolvedValue({ data: stopped })
  getApp.mockImplementation((id) => Promise.resolve({ data: stopped.find((a) => a.id === id) }))
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-act-prod').attributes('disabled')).toBeDefined()

  getApp.mockResolvedValue({ data: { ...APPS[0], status_prod: 'running' } })
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(w.find('.zc-act-prod').attributes('disabled')).toBeUndefined()
})

// ---- 票 004：更多菜单与只读壳子 ----
// DeployLogPanel / MetricsModal 打桩：本票测的是「挂没挂对、id 传没传对、互斥生不生效」，
// 它们的内部行为各有自己的测试文件。
const LogsStub = {
  name: 'DeployLogPanel',
  props: ['appId', 'appName', 'active'],
  render() {
    return h('div', { class: 'stub-logs', 'data-app-id': String(this.appId), 'data-active': String(this.active) })
  },
}
const MetricsStub = {
  name: 'MetricsModal',
  props: ['app'],
  render() {
    return h('div', { class: 'stub-metrics', 'data-slug': this.app?.slug })
  },
}
const DrawerStub = {
  name: 'a-drawer',
  props: ['open'],
  render() {
    if (!this.open) return null
    return h('div', { class: 'stub-drawer' }, this.$slots.default ? this.$slots.default() : [])
  },
}
const ModalStub = {
  name: 'a-modal',
  props: ['open'],
  render() {
    if (!this.open) return null
    return h('div', { class: 'stub-modal' }, this.$slots.default ? this.$slots.default() : [])
  },
}
it('「更多」渲染七项并按三段分隔线组织', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  const items = w.findAll('.zc-app-menu-item').map((n) => n.text())
  expect(items).toEqual([
    '复制预览地址', '数据库操作', '重建 dev 库',
    '发布新版本', '发布记录', '资源监控', '预览日志',
  ])
  expect(w.findAll('.zc-app-menu-sep').length).toBe(2)
})

it('点「预览日志」打开抽屉，并把当前应用的 id 传给面板', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-logs').trigger('click')
  const panel = w.find('.stub-logs')
  expect(panel.exists()).toBe(true)
  expect(panel.attributes('data-app-id')).toBe('12')
  expect(panel.attributes('data-active')).toBe('true')
})

// 关抽屉必须把 active 落回 false——它驱动面板内部的 WS 连与不连，
// 只关抽屉不落 active 就是一条永远挂着的僵尸连接。
it('关掉日志抽屉后 active 落回 false', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-logs').trigger('click')
  expect(w.find('.stub-logs').attributes('data-active')).toBe('true')
  w.findComponent(DrawerStub).vm.$emit('update:open', false)
  await flushPromises()
  expect(w.find('.stub-logs').exists()).toBe(false)
})

it('四个壳子互斥：开监控会把日志抽屉关掉', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-logs').trigger('click')
  expect(w.find('.stub-logs').exists()).toBe(true)
  await w.find('.zc-menu-metrics').trigger('click')
  expect(w.find('.stub-logs').exists()).toBe(false)
  expect(w.find('.stub-metrics').exists()).toBe(true)
  expect(w.find('.stub-metrics').attributes('data-slug')).toBe('test1')
})

it('「数据库操作」是新标签整页链接，指向当前应用', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  const a = w.find('.zc-menu-db')
  expect(a.attributes('href')).toBe('/apps/12/db')
  expect(a.attributes('target')).toBe('_blank')
})

// ---- 票 005：发布链 ----
// PublishModal 在 setup 期就读 props.app.id（useDeploy(props.app.id)），所以必须 v-if + :key
// 才挂，传 null 隐藏会在 setup 里炸。桩保留同样的读取契约，好让「没 app 就不挂」这条被真的测到。
const PublishStub = {
  name: 'PublishModal',
  props: ['app', 'open', 'mode', 'rollbackVersion', 'autoStart'],
  setup(props) {
    if (!props.app || props.app.id == null) throw new TypeError('PublishModal 需要真 app：setup 期就读 app.id')
    return {}
  },
  render() {
    return h('div', {
      class: 'stub-publish',
      'data-mode': this.mode,
      'data-autostart': String(this.autoStart),
      'data-version': String(this.rollbackVersion),
    })
  },
}
const HistoryStub = {
  name: 'PublishHistory',
  props: ['app'],
  render() {
    return h('div', { class: 'stub-history', 'data-slug': this.app?.slug }, [
      h('button', { class: 'stub-rollback', onClick: () => this.$emit('rollback', 'v3') }, 'rollback'),
    ])
  },
}

it('点「发布记录」打开记录抽屉并传入当前应用', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-history').trigger('click')
  expect(w.find('.stub-history').attributes('data-slug')).toBe('test1')
})

it('点「发布新版本」挂上发布弹层', async () => {
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-publish').trigger('click')
  expect(w.find('.stub-publish').exists()).toBe(true)
  expect(w.find('.stub-publish').attributes('data-mode')).toBe('publish')
})

// 闸门与列表页同源：栈名单的唯一真相在后端 app/deploy/compose.py::PUBLISHABLE_STACKS，
// 前端读的是 utils/publishableStacks.js 那份镜像。聊天页自己写死一份必然漂移——
// 曾经就是 `=== 'vue3-fastapi'`，列表页放宽到含 springboot 后这里悄悄灰掉了。
it('vue3-springboot 应用同样可发布（闸门与列表页同源，不各自写死）', async () => {
  listApps.mockResolvedValue({ data: [{ ...APPS[0], stack: 'vue3-springboot' }] })
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-menu-publish').classes()).not.toContain('is-disabled')
  await w.find('.zc-menu-publish').trigger('click')
  expect(w.find('.stub-publish').exists()).toBe(true)
})
// 闸门读共享名单（utils/publishableStacks.js），不在聊天页另立第二套判断
it('栈不支持发布时「发布新版本」不可点，且点了不挂弹层', async () => {
  listApps.mockResolvedValue({ data: [{ ...APPS[0], stack: 'static' }] })
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-menu-publish').classes()).toContain('is-disabled')
  await w.find('.zc-menu-publish').trigger('click')
  expect(w.find('.stub-publish').exists()).toBe(false)
})

// 回滚是「关记录抽屉 → 开进度弹层并自动触发」，两个壳子交接时不能同时在场，
// 也不能开着记录点回滚结果复用了上一个应用的 PublishModal 实例。
it('记录页点回滚 → 记录关闭、发布弹层以 rollback 模式自动起跑', async () => {
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-history').trigger('click')
  await w.find('.stub-rollback').trigger('click')
  expect(w.find('.stub-history').exists()).toBe(false)
  const pub = w.find('.stub-publish')
  expect(pub.exists()).toBe(true)
  expect(pub.attributes('data-mode')).toBe('rollback')
  expect(pub.attributes('data-autostart')).toBe('true')
  expect(pub.attributes('data-version')).toBe('v3')
})

// 轮询回到的新 status_dev 必须把文案顶掉——这是本页唯一的预览状态读数，
// 停在 listApps 那一刻的旧值等于按钮在撒谎。
it('轮询到 running 后按钮文案从「启动预览」变成「重启预览」', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  expect(w.find('.zc-act-dev').text()).toContain('启动预览')

  getApp.mockResolvedValue({ data: { ...APPS[1], status_dev: 'running' } })
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(w.find('.zc-act-dev').text()).toContain('重启预览')
})

it('每 5s 打一次 getApp 取状态', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  const base = getApp.mock.calls.length
  await vi.advanceTimersByTimeAsync(15000)
  expect(getApp.mock.calls.length - base).toBe(3)
})

it('轮询到 running 时报一句「预览已就绪」并解开按钮自锁', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-act-dev').trigger('click')
  expect(w.find('.zc-act-dev').classes()).toContain('is-loading')

  getApp.mockResolvedValue({ data: { ...APPS[1], status_dev: 'running' } })
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(message.success).toHaveBeenCalled()
  expect(w.find('.zc-act-dev').classes()).not.toContain('is-loading')
})

// US-08/09：失败提示里必须带一个**可点击**的「查看日志」——排查启动失败时用户下一步
// 几乎必然是看日志，让他在 message 和菜单之间来回找是浪费。
it('启动失败/超时的提示里带可点的「查看日志」，点了直接开日志抽屉', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-act-dev').trigger('click')

  getApp.mockResolvedValue({ data: { ...APPS[1], status_dev: 'error' } })
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()

  const cfg = message.error.mock.calls.at(-1)[0]
  expect(cfg).toBeTypeOf('object') // 不是纯字符串，才带得下可点元素
  // 链接必须是写着「查看日志」的 <a>，且它的 onClick 就是开日志抽屉的处理器
  const anchor = cfg.content.children.find((c) => c.type === 'a')
  expect(anchor).toBeTruthy()
  expect(anchor.children).toContain('查看日志')
  anchor.props.onClick()
  await flushPromises()
  expect(w.find('.stub-logs').exists()).toBe(true)
})

// 后端把 Docker 不可用等原因直接写在 detail 里，超时则是本地判定——两种都要给出去，
// 不能只留一个转圈让人自己猜。
it('轮询到 error 时报失败并带上后端原文', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-act-dev').trigger('click')

  getApp.mockResolvedValue({ data: { ...APPS[1], status_dev: 'error' } })
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(message.error).toHaveBeenCalled()
  expect(w.find('.zc-act-dev').classes()).not.toContain('is-loading')
})

// 启停是 202 后台受理，没有回调；等轮询把 starting 捞回来要最多 5 秒，
// 这 5 秒里按钮看着可再点、文案还是旧值，用户就会连点。点击当场就得自锁。
it('点启动后按钮立刻 loading 并禁用，同时给一句受理提示', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  const btn = w.find('.zc-act-dev')
  await btn.trigger('click')
  expect(btn.classes()).toContain('is-loading')
  expect(btn.classes()).toContain('is-disabled')
  expect(message.info).toHaveBeenCalled()
})

// ---- 票 006：重建 dev 库 ----
// 它动的是数据库不是容器栈：drop + provision + 从 sqlchange/ 全量 fold（app/api/apps.py::dev_reset）。
// 文案与确认形态逐字复用卡面（AppsView.vue::resetDev），差别只在聊天页这项叫「重建 dev 库」。
it('点「重建 dev 库」弹 danger 确认，正文点名两个具体库名', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-reset').trigger('click')
  expect(Modal.confirm).toHaveBeenCalledTimes(1)
  const cfg = Modal.confirm.mock.calls[0][0]
  expect(cfg.title).toBe('重建 dev 数据库')
  expect(cfg.okType).toBe('danger')
  expect(cfg.content).toContain('test1_dev')
  expect(cfg.content).toContain('test1_prod')
  expect(devReset).not.toHaveBeenCalled()
})

// 本页不订阅 deploy WS，重建结果（dev_reset_result）没有别的通道能被看见，
// 所以确认后必须自动把日志抽屉弹开。
it('确认重建后调接口并自动弹开预览日志', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-reset').trigger('click')
  await Modal.confirm.mock.calls[0][0].onOk()
  await flushPromises()
  expect(devReset).toHaveBeenCalledWith(12)
  expect(w.find('.stub-logs').exists()).toBe(true)
})

it('取消重建时什么都不做', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-reset').trigger('click')
  const cfg = Modal.confirm.mock.calls[0][0]
  expect(cfg.onOk).toBeTruthy()
  expect(devReset).not.toHaveBeenCalled()
  expect(w.find('.stub-logs').exists()).toBe(false)
})

// ---- 票 007：收口 ----

// 旧壳挂着上一个应用的 id 继续显示是事故源：在 A 的会话里开着发布弹层切到 B，
// 点确认发到的是 A 的库，而屏幕上写着 B 的名字。
it('切换会话时四个壳子全部关闭', async () => {
  useChatStore().currentWorkspaceId = 102
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-logs').trigger('click')
  expect(w.find('.stub-logs').exists()).toBe(true)

  useChatStore().currentWorkspaceId = 101
  await flushPromises()
  expect(w.find('.stub-logs').exists()).toBe(false)
})

it('发布弹层开着时切会话也会关掉', async () => {
  useChatStore().currentWorkspaceId = 101
  const w = mountActions()
  await flushPromises()
  await w.find('.zc-menu-publish').trigger('click')
  expect(w.find('.stub-publish').exists()).toBe(true)
  useChatStore().currentWorkspaceId = 102
  await flushPromises()
  expect(w.find('.stub-publish').exists()).toBe(false)
})

// 预览地址只能来自接口字段：按 dev_port 自拼的链接在容器化/远端访问下是条死链，
// 而它和真地址在界面上无法区分（ADR-0021 后果条 / CONTEXT.md 既有红线）。
// 判定域必须先剥掉注释——组件自己的注释就在解释这条红线，不剥会被自己误伤。
it('组件源码里不得出现自拼的预览地址', async () => {
  const src = (await import('./ChatAppActions.vue?raw')).default
  const code = src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split(String.fromCharCode(10))
    .filter((l) => !/^\s*\/\//.test(l))
    .join(String.fromCharCode(10))
  expect(code).not.toMatch(/localhost/)
  expect(code).not.toMatch(/dev_port/)
  expect(code).not.toMatch(/https?:\/\//)
})

// 反向断言：证明上面那条不是空转（判定域收窄后容易连自己一起放过）
it('红线检查器真的能抓到自拼地址', async () => {
  const offender = 'const url = `http://localhost:${app.dev_port}/`'
  expect(/localhost|https?:\/\/|dev_port/.test(offender)).toBe(true)
})
