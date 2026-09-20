// @vitest-environment happy-dom
// 新会话弹窗的行为契约（2026-09-20 需求）：以后只有应用——
//   ① 不再提供「新建工作区 / 目录路径 / 浏览目录」，手动挂目录的入口整体下线；
//   ② 只能从已有应用里选一个（选项由父级 SideBar 传入，值即应用关联的 workspace_id）；
//   ③ 一个应用都没有时，不给创建按钮放行，只给去「应用」页新建的引导。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  createSession: vi.fn(), createWorkspace: vi.fn(), listApps: vi.fn(),
  listWorkspaces: vi.fn(), getMessages: vi.fn(), getSessionDetail: vi.fn(),
  listModels: vi.fn(), patchSessionModel: vi.fn(), browseDir: vi.fn(),
  wsUrl: vi.fn(() => 'ws://test'),
}))
vi.mock('ant-design-vue', () => ({ message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

import { createSession, createWorkspace } from '../api'
import NewSessionModal from './NewSessionModal.vue'
import { useChatStore } from '../stores/chat'

const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})
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
      class: 'stub-input', value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value),
    })
  },
}
const STUBS = {
  'a-modal': {
    name: 'a-modal', props: ['open'],
    render() {
      if (!this.open) return null
      return h('div', { class: 'stub-modal' }, [
        ...(this.$slots.default ? this.$slots.default() : []),
        h('button', { class: 'stub-modal-ok', onClick: () => this.$emit('ok') }, 'ok'),
      ])
    },
  },
  'a-select': SelectStub,
  'a-input': InputStub,
  'a-divider': slotDiv('a-divider'),
  'a-button': {
    name: 'a-button',
    render() {
      return h('button', { class: 'stub-btn', onClick: (e) => this.$emit('click', e) },
        this.$slots.default ? this.$slots.default() : [])
    },
  },
  'router-link': { name: 'router-link', props: ['to'], render() { return h('a', this.$slots.default?.()) } },
}

const APPS = [
  { id: 7, workspace_id: 11, name: '来料登记', slug: 'lailiao' },
  { id: 8, workspace_id: 12, name: '进销存', slug: 'jxc' },
]

async function mountModal(apps = APPS) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(NewSessionModal, {
    props: { open: true, apps },
    global: { plugins: [pinia], stubs: STUBS },
  })
  await flushPromises()
  const store = useChatStore()
  store.openSession = vi.fn()
  return { wrapper, store }
}

beforeEach(() => {
  vi.clearAllMocks()
  createSession.mockResolvedValue({ data: { id: 900, workspace_id: 11, title: '新会话' } })
})

describe('NewSessionModal 只能选应用', () => {
  it('没有目录路径输入、没有浏览目录、没有新建工作区文案', async () => {
    const { wrapper } = await mountModal()
    const text = wrapper.find('.stub-modal').text()
    expect(text).not.toContain('浏览目录')
    expect(text).not.toContain('新建工作区')
    expect(text).not.toContain('目录路径')
    expect(wrapper.find('.zc-ns-path').exists()).toBe(false)
    expect(wrapper.findAll('.stub-input')).toHaveLength(1)   // 只剩会话名称一个输入框
  })

  it('下拉选项来自应用列表，选中值就是应用关联的 workspace_id', async () => {
    const { wrapper } = await mountModal()
    const options = wrapper.findAll('.stub-option')
    expect(options.map((o) => o.text())).toEqual(['来料登记', '进销存'])
    await options[1].trigger('click')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith({ workspace_id: 12 })
  })

  it('绝不创建新工作区：整个流程不调 createWorkspace', async () => {
    const { wrapper } = await mountModal()
    await wrapper.find('.stub-option').trigger('click')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(createWorkspace).not.toHaveBeenCalled()
  })

  it('填了会话名称就带着标题创建，并打开该会话（带 workspace_id 供右栏渲染门）', async () => {
    const { wrapper, store } = await mountModal()
    await wrapper.find('.stub-option').trigger('click')
    await wrapper.find('.stub-input').setValue('录入表单怎么搭')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith({ workspace_id: 11, title: '录入表单怎么搭' })
    expect(store.openSession).toHaveBeenCalledWith(900, 11)
  })

  it('没选应用时不放行：既不建会话也不报错', async () => {
    const { wrapper } = await mountModal()
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(createSession).not.toHaveBeenCalled()
  })

  it('一个应用都没有时给出去「应用」页新建的引导', async () => {
    const { wrapper } = await mountModal([])
    expect(wrapper.find('.stub-modal').text()).toContain('应用')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(createSession).not.toHaveBeenCalled()
  })
})
