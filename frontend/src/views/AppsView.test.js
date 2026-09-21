// @vitest-environment happy-dom
// Task 10：AppsView 发布接线（最小挂载断言）——M2 占位文案撤掉、发布/历史入口上卡、
// 栈闸门（非 vue3-fastapi 发布按钮置灰带说明）。弹层内部行为在 PublishModal.test 覆盖。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: {
    listApps: vi.fn(), createApp: vi.fn(), validateAppSlug: vi.fn(),
    devStart: vi.fn(), devStop: vi.fn(), devRestart: vi.fn(), devStatus: vi.fn(),
    prodStart: vi.fn(), prodStop: vi.fn(), prodRestart: vi.fn(), prodStatus: vi.fn(),
    appUrls: vi.fn(), publishApp: vi.fn(), rollbackApp: vi.fn(),
    appRecords: vi.fn(), deleteRecord: vi.fn(), systemNics: vi.fn(), putConfig: vi.fn(),
    wsUrl: vi.fn(() => 'ws://test'),
  },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn() } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

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
})
