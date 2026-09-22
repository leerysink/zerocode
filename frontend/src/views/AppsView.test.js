// @vitest-environment happy-dom
// Task 10：AppsView 发布接线（最小挂载断言）——M2 占位文案撤掉、发布/历史入口上卡、
// 栈闸门（非 vue3-fastapi 发布按钮置灰带说明）。弹层内部行为在 PublishModal.test 覆盖。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: {
    listApps: vi.fn(), createApp: vi.fn(), validateAppSlug: vi.fn(),
    devStart: vi.fn(), devStop: vi.fn(), devRestart: vi.fn(), devReset: vi.fn(), devStatus: vi.fn(),
    prodStart: vi.fn(), prodStop: vi.fn(), prodRestart: vi.fn(), prodStatus: vi.fn(),
    appUrls: vi.fn(), publishApp: vi.fn(), rollbackApp: vi.fn(),
    appRecords: vi.fn(), deleteRecord: vi.fn(), systemNics: vi.fn(), putConfig: vi.fn(),
    releaseToStore: vi.fn(), renameApp: vi.fn(), deleteApp: vi.fn(), openFolder: vi.fn(),
    wsUrl: vi.fn(() => 'ws://test'),
  },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn() } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)
// AppsView 顶层调 useConfigStore()：无 pinia 会挂载即抛，这里给一个只读桩（openFolderMode=auto）
vi.mock('../stores/config', () => ({
  useConfigStore: () => ({ ensureLoaded: vi.fn(async () => {}), form: { openFolderMode: 'auto' } }),
}))

import AppsView from './AppsView.vue'

const APPS = [
  { id: 1, name: '甲', slug: 'aaa', stack: 'vue3-fastapi', scale: 'small', dev_port: 8301,
    prod_port: 8401, status_dev: 'stopped', status_prod: 'running', current_version: 'b20260920100000' },
  { id: 2, name: '乙', slug: 'bbb', stack: 'static', scale: 'small', dev_port: 8302,
    prod_port: 8402, status_dev: 'stopped', status_prod: 'stopped', current_version: null },
]

const slot = (name) => ({
  name, props: ['disabled', 'title', 'type', 'size', 'href', 'target'],
  render() {
    const attrs = { class: `stub-${name}` }
    if (this.disabled) attrs.disabled = ''
    if (this.title) attrs.title = this.title
    if (this.href) attrs.href = this.href
    return h(name === 'a-button' ? 'button' : 'div', attrs, this.$slots.default?.())
  },
})
const STUBS = {
  'a-card': { name: 'a-card', render() {
    return h('div', { class: 'stub-card' }, [
      this.$slots.title?.(), this.$slots.extra?.(), this.$slots.default?.()]) } },
  'a-space': slot('a-space'),
  'a-button': slot('a-button'),
  'a-tag': slot('a-tag'),
  'a-modal': slot('a-modal'),
  'a-drawer': { name: 'a-drawer', props: ['open'], render() {
    return this.open ? h('div', { class: 'stub-drawer' }, this.$slots.default?.()) : null } },
  'a-spin': slot('a-spin'),
  'a-empty': slot('a-empty'),
  'a-steps': slot('a-steps'),
  'a-form': slot('a-form'),
  'a-form-item': slot('a-form-item'),
  'a-input': slot('a-input'),
  'a-select': slot('a-select'),
  'a-tooltip': slot('a-tooltip'),
  'a-dropdown': { name: 'a-dropdown', render() {
    return h('div', { class: 'stub-dropdown' }, [this.$slots.default?.(), this.$slots.overlay?.()]) } },
  BackButton: { name: 'BackButton', render() { return h('button', { class: 'stub-back' }, '返回') } },
  WorkspaceExplorer: { name: 'WorkspaceExplorer', render() { return h('div', { class: 'stub-explorer' }) } },
  PublishModal: { name: 'PublishModal', render() { return h('div', { class: 'stub-pub-modal' }) } },
  PublishHistory: { name: 'PublishHistory', render() { return h('div', { class: 'stub-pub-history' }) } },
}

async function mountView() {
  mocks.api.listApps.mockResolvedValue({ data: APPS })
  const wrapper = mount(AppsView, { global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(window, 'setInterval').mockReturnValue(0)
})

describe('AppsView 发布接线', () => {
  it('M2 占位文案已撤，正式版行展示 prod 状态与版本', async () => {
    const wrapper = await mountView()
    const text = wrapper.text()
    expect(text).not.toContain('正式发布：M2 即将上线')
    const cards = wrapper.findAll('.stub-card')
    expect(cards[0].text()).toContain('运行中')
    expect(cards[0].text()).toContain('b20260920100000')
  })

  it('卡片操作行：发布 / 发布历史 / 正式环境启停入口', async () => {
    const wrapper = await mountView()
    const text = wrapper.findAll('.stub-card')[0].text()
    expect(text).toContain('发布')
    expect(text).toContain('发布历史')
    expect(text).toContain('停止正式')   // running → 提供停止
    expect(wrapper.findAll('.stub-card')[1].text()).toContain('启动正式')
  })

  it('栈闸门：非 vue3-fastapi 的发布按钮置灰带说明 title', async () => {
    const wrapper = await mountView()
    const pubBtns = wrapper.findAll('.stub-card')[1].findAll('.stub-a-button')
      .filter((b) => b.text().includes('发布') && !b.text().includes('历史'))
    expect(pubBtns.length).toBeGreaterThan(0)
    expect(pubBtns[0].attributes('disabled')).toBeDefined()
    expect(pubBtns[0].attributes('title')).toContain('即将上线')
  })

  it('点发布打开进度弹层（PublishModal 挂载并收到 app）', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('.stub-pub-modal').exists()).toBe(false)
    const btn = wrapper.findAll('.stub-card')[0].findAll('.stub-a-button')
      .find((b) => b.text() === '发布')
    await btn.trigger('click')
    expect(wrapper.find('.stub-pub-modal').exists()).toBe(true)
  })

  it('☰ 菜单「重建 dev」：弹确认框，确认后调 devReset（202 受理）', async () => {
    const wrapper = await mountView()
    const item = wrapper.findAll('.stub-card')[0].find('.zc-menu-reset')
    expect(item.exists()).toBe(true)
    await item.trigger('click')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    const opts = mocks.antd.Modal.confirm.mock.calls[0][0]
    await opts.onOk()
    expect(mocks.api.devReset).toHaveBeenCalledWith(1)
  })
})

// ---------- M3-a 商店接线：角标 / 发布到商店入口（最小追加，不动既有布局） ----------
const STORE_APPS = [
  { id: 1, name: '甲', slug: 'aaa', stack: 'vue3-fastapi', scale: 'small', dev_port: 8301,
    prod_port: 8401, status_dev: 'stopped', status_prod: 'running', current_version: 'b1' },
  { id: 2, name: '乙', slug: 'bbb', stack: 'vue3-fastapi', scale: 'small', dev_port: 8302,
    prod_port: 8402, status_dev: 'stopped', status_prod: 'stopped', current_version: null },
  { id: 3, name: '丙i', slug: 'ccci', stack: 'vue3-fastapi', scale: 'small', dev_port: 8303,
    prod_port: 8403, status_dev: 'stopped', status_prod: 'running', current_version: 'b1',
    store: { release_slug: 'aaa', release_version: 'b1', release_title: '甲', channel: 'managed' } },
]

// 上架弹窗需要真一点的桩：open 才渲染、带 ok 按钮、输入框回写 v-model:value
const STORE_STUBS = {
  ...STUBS,
  'a-modal': { name: 'a-modal', props: ['open'], render() {
    if (!this.open) return null
    return h('div', { class: 'stub-modal' }, [
      this.$slots.default?.(),
      h('button', { class: 'stub-modal-ok', onClick: () => this.$emit('ok') }, 'ok')]) } },
  'a-input': { name: 'a-input', props: ['value'], render() {
    return h('input', { class: 'stub-input', value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
  'a-textarea': { name: 'a-textarea', props: ['value'], render() {
    return h('textarea', { class: 'stub-textarea', value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
}

async function mountStoreView() {
  mocks.api.listApps.mockResolvedValue({ data: STORE_APPS })
  const wrapper = mount(AppsView, { global: { stubs: STORE_STUBS } })
  await flushPromises()
  return wrapper
}

describe('AppsView 商店接线', () => {
  it('商店实例卡片带「来自商店」角标，普通应用没有', async () => {
    const wrapper = await mountStoreView()
    const cards = wrapper.findAll('.stub-card')
    expect(cards[2].text()).toContain('来自商店')
    expect(cards[0].text()).not.toContain('来自商店')
  })

  it('入口闸门：发布过的非商店应用 ☰ 菜单里有「发布到商店」；没发布过/商店实例没有', async () => {
    const wrapper = await mountStoreView()
    const has = (i) => wrapper.findAll('.stub-card')[i].find('.zc-menu-release').exists()
    expect(has(0)).toBe(true)
    expect(has(1)).toBe(false)   // current_version=null
    expect(has(2)).toBe(false)   // 商店实例不重复上架
  })

  it('发布到商店：菜单项点开弹窗提交调 releaseToStore(appId, {title, summary})', async () => {
    mocks.api.releaseToStore.mockResolvedValue({ data: { id: 9 } })
    const wrapper = await mountStoreView()
    await wrapper.findAll('.stub-card')[0].find('.zc-menu-release').trigger('click')
    const modal = wrapper.find('.stub-modal')
    expect(modal.exists()).toBe(true)
    await modal.find('.stub-input').setValue('甲商店版')
    await modal.find('.stub-textarea').setValue('简介文本')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(mocks.api.releaseToStore).toHaveBeenCalledWith(1, { title: '甲商店版', summary: '简介文本' })
  })
})

// ---------- 卡片右上 ☰ 折叠菜单：重命名/打开工程目录/复制预览地址/查看日志/删除 ----------
describe('AppsView 卡片折叠菜单', () => {
  it('卡片右上 ☰ 菜单收纳预览/重建/历史 + 五项工具操作', async () => {
    const wrapper = await mountView()
    const menu = wrapper.findAll('.stub-card')[0].find('.stub-dropdown')
    expect(menu.exists()).toBe(true)
    const text = menu.text()
    for (const label of ['预览', '重建 dev', '发布历史', '重命名', '打开工程目录', '复制预览地址', '查看日志', '删除应用'])
      expect(text).toContain(label)
  })

  it('菜单点「删除应用」→ 弹确认框，确认后调 deleteApp', async () => {
    mocks.api.deleteApp.mockResolvedValue({ data: { kept: { root_path: 'apps/aaa' } } })
    mocks.api.listApps.mockResolvedValue({ data: APPS })
    const wrapper = mount(AppsView, { global: { stubs: STORE_STUBS } })
    await flushPromises()
    await wrapper.findAll('.zc-menu-del')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.stub-modal').exists()).toBe(true)
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(mocks.api.deleteApp).toHaveBeenCalledWith(1)
  })
})
