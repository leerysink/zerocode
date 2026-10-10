// @vitest-environment happy-dom
// MemoryPanel（Ticket 008）：CRUD + type 分组 + 搜索 + 启用开关 + 截断条幅 + 空态。
// 桩件方案对齐 SkillsView.test.js（vi.mock antd，不挂真 Antd）；id 全程字符串（19 位大整数不得 Number()）。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: {
    listMemories: vi.fn(() => Promise.resolve({ data: [], headers: {} })),
    createMemory: vi.fn(() => Promise.resolve({ data: { id: '1' } })),
    updateMemory: vi.fn(() => Promise.resolve({ data: {} })),
    deleteMemory: vi.fn(() => Promise.resolve({ data: {} })),
  },
  antd: {
    message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    Modal: { confirm: vi.fn() },
  },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

import { listMemories, createMemory, updateMemory, deleteMemory } from '../api'
import { message, Modal } from 'ant-design-vue'
import MemoryPanel from './MemoryPanel.vue'

const slot = (name) => ({
  name, props: ['disabled', 'title', 'type', 'size', 'loading', 'value', 'checked', 'placeholder'],
  render() {
    const attrs = { class: `stub-${name}` }
    if (this.disabled) attrs.disabled = ''
    if (this.value !== undefined) attrs.value = this.value
    return h(name === 'a-button' ? 'button' : 'div', attrs, this.$slots.default?.())
  },
})

const STUBS = {
  'a-button': slot('a-button'),
  'a-input': { name: 'a-input', props: ['value', 'placeholder'], render() {
    return h('input', { class: 'stub-input', value: this.value, placeholder: this.placeholder,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
  'a-input-search': { name: 'a-input-search', props: ['value', 'placeholder'], render() {
    return h('input', { class: 'stub-search', value: this.value, placeholder: this.placeholder,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
  'a-textarea': { name: 'a-textarea', props: ['value', 'rows'], render() {
    return h('textarea', { class: 'stub-textarea', value: this.value, rows: this.rows,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
  'a-select': { name: 'a-select', props: ['value'], render() {
    return h('select', { class: 'stub-select', value: this.value,
      onChange: (e) => this.$emit('update:value', e.target.value) }, this.$slots.default?.()) } },
  'a-select-option': { name: 'a-select-option', props: ['value'], render() {
    return h('option', { class: 'stub-option', value: this.value }, this.$slots.default?.()) } },
  'a-switch': { name: 'a-switch', props: ['checked', 'size', 'disabled'], render() {
    const attrs = { class: 'stub-switch', type: 'checkbox', checked: this.checked,
      onChange: () => this.$emit('change') }
    if (this.disabled) attrs.disabled = ''
    return h('input', attrs) } },
  'a-spin': slot('a-spin'),
  'a-empty': slot('a-empty'),
  'a-alert': { name: 'a-alert', props: ['type', 'showIcon'], render() {
    return h('div', { class: 'stub-alert' }, [this.$slots.message?.()]) } },
  'a-modal': slot('a-modal'),
  'PlusOutlined': true,
  'ReloadOutlined': true,
  'SearchOutlined': true,
}

async function mountPanel() {
  const wrapper = mount(MemoryPanel, {
    props: { workspaceId: 1 },
    global: { stubs: STUBS },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.api.listMemories.mockResolvedValue({ data: [], headers: {} })
})

describe('MemoryPanel - 空态与加载', () => {
  it('空列表显示空态', async () => {
    const wrapper = await mountPanel()
    expect(wrapper.find('.zc-mem-empty').exists() || wrapper.text().includes('没有记忆')).toBe(true)
    expect(listMemories).toHaveBeenCalledWith(1)
  })

  it('加载失败显示错误', async () => {
    mocks.api.listMemories.mockRejectedValue({ response: { data: { detail: '工作区不存在' } } })
    const wrapper = await mountPanel()
    expect(message.error).toHaveBeenCalledWith('工作区不存在')
  })
})

describe('MemoryPanel - 列表与分组', () => {
  it('按 type 分组显示', async () => {
    const data = [
      { id: '1', type: 'fact', content: '事实 1', title: 'F1', enabled: true },
      { id: '2', type: 'preference', content: '偏好 1', title: 'P1', enabled: true },
      { id: '3', type: 'fact', content: '事实 2', title: 'F2', enabled: false },
    ]
    mocks.api.listMemories.mockResolvedValue({ data, headers: {} })
    const wrapper = await mountPanel()
    const text = wrapper.text()
    expect(text).toContain('事实')
    expect(text).toContain('偏好')
    expect(text).toContain('F1')
    expect(text).toContain('P1')
    expect(text).toContain('F2')
  })

  it('id 是字符串不被 Number() 截断（开关透传）', async () => {
    const bigId = '9223372036854775807' // 2^63 - 1，过 Number() 会变
    const data = [{ id: bigId, type: 'fact', content: '大整数 id', title: 'Big', enabled: true }]
    mocks.api.listMemories.mockResolvedValue({ data, headers: {} })
    mocks.api.updateMemory.mockResolvedValue({ data: { enabled: false } })
    const wrapper = await mountPanel()
    expect(wrapper.text()).toContain('Big')
    // 开关翻转应传字符串 bigId，不得变成数字
    const switchInput = wrapper.find('.stub-switch')
    await switchInput.trigger('change')
    await flushPromises()
    expect(updateMemory).toHaveBeenCalledWith(1, bigId, { enabled: false })
  })
})

describe('MemoryPanel - 搜索', () => {
  it('关键字过滤标题与正文', async () => {
    const data = [
      { id: '1', type: 'fact', content: '包含关键词的正文', title: '匹配标题', enabled: true },
      { id: '2', type: 'fact', content: '无关内容', title: '无关标题', enabled: true },
    ]
    mocks.api.listMemories.mockResolvedValue({ data, headers: {} })
    const wrapper = await mountPanel()
    const searchInput = wrapper.find('.stub-search')
    await searchInput.setValue('匹配')
    await flushPromises()
    expect(wrapper.text()).toContain('匹配标题')
    expect(wrapper.text()).not.toContain('无关标题')
  })
})

describe('MemoryPanel - 启用开关', () => {
  it('翻转 enabled 调用 updateMemory 并以服务端回填', async () => {
    const data = [{ id: '1', type: 'fact', content: '测试', title: 'T', enabled: true }]
    mocks.api.listMemories.mockResolvedValue({ data, headers: {} })
    mocks.api.updateMemory.mockResolvedValue({ data: { enabled: false } })
    const wrapper = await mountPanel()
    const switchInput = wrapper.find('.stub-switch')
    await switchInput.trigger('change')
    await flushPromises()
    expect(updateMemory).toHaveBeenCalledWith(1, '1', { enabled: false })
  })

  it('在飞互斥：第一次未返回时第二点击被忽略', async () => {
    const data = [
      { id: '1', type: 'fact', content: 'A', title: 'A', enabled: true },
      { id: '2', type: 'fact', content: 'B', title: 'B', enabled: true },
    ]
    mocks.api.listMemories.mockResolvedValue({ data, headers: {} })
    let resolve
    mocks.api.updateMemory.mockImplementation(() => new Promise((r) => { resolve = r }))
    const wrapper = await mountPanel()
    const switches = wrapper.findAll('.stub-switch')
    await switches[0].trigger('change')
    await switches[1].trigger('change') // 在飞应被忽略
    expect(updateMemory).toHaveBeenCalledTimes(1)
    resolve({ data: { enabled: false } })
    await flushPromises()
  })
})

describe('MemoryPanel - 删除', () => {
  it('删除弹确认框并调用 deleteMemory', async () => {
    const data = [{ id: '1', type: 'fact', content: '测试', title: '要删的', enabled: true }]
    mocks.api.listMemories.mockResolvedValue({ data, headers: {} })
    const wrapper = await mountPanel()
    const delBtn = wrapper.findAll('button').find((b) => b.text() === '删除')
    await delBtn.trigger('click')
    expect(Modal.confirm).toHaveBeenCalled()
    // 模拟确认
    const onOk = Modal.confirm.mock.calls[0][0].onOk
    await onOk()
    expect(deleteMemory).toHaveBeenCalledWith(1, '1')
  })
})

describe('MemoryPanel - 截断条幅', () => {
  it('X-Memory-Truncated 为 true 时显示条幅', async () => {
    mocks.api.listMemories.mockResolvedValue({
      data: [{ id: '1', type: 'fact', content: 'x', title: 't', enabled: true }],
      headers: { 'x-memory-truncated': 'true', 'x-memory-limit': '200' },
    })
    const wrapper = await mountPanel()
    expect(wrapper.text()).toContain('仅展示最近 200 条')
  })

  it('X-Memory-Truncated 为 false 时不显示条幅', async () => {
    mocks.api.listMemories.mockResolvedValue({
      data: [{ id: '1', type: 'fact', content: 'x', title: 't', enabled: true }],
      headers: { 'x-memory-truncated': 'false', 'x-memory-limit': '200' },
    })
    const wrapper = await mountPanel()
    expect(wrapper.text()).not.toContain('仅展示最近')
  })
})

describe('MemoryPanel - 新增', () => {
  it('新增表单提交调用 createMemory', async () => {
    const wrapper = await mountPanel()
    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('新增'))
    await addBtn.trigger('click')
    await flushPromises()
    // 表单应出现
    expect(wrapper.find('.zc-mem-form').exists()).toBe(true)
    // 填写并提交
    const textarea = wrapper.find('.stub-textarea')
    await textarea.setValue('新记忆内容')
    const submitBtn = wrapper.findAll('button').find((b) => b.text() === '添加')
    await submitBtn.trigger('click')
    await flushPromises()
    expect(createMemory).toHaveBeenCalledWith(1, expect.objectContaining({
      type: 'fact',
      content: '新记忆内容',
    }))
  })

  it('内容为空时拒提交', async () => {
    const wrapper = await mountPanel()
    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('新增'))
    await addBtn.trigger('click')
    await flushPromises()
    const submitBtn = wrapper.findAll('button').find((b) => b.text() === '添加')
    await submitBtn.trigger('click')
    expect(message.warning).toHaveBeenCalledWith('内容不能为空')
    expect(createMemory).not.toHaveBeenCalled()
  })
})

describe('MemoryPanel - 编辑保存', () => {
  it('编辑表单提交调用 updateMemory，title 传空串不传 null', async () => {
    const data = [{ id: '42', type: 'fact', content: '原文', title: '旧标题', enabled: true }]
    mocks.api.listMemories.mockResolvedValue({ data, headers: {} })
    mocks.api.updateMemory.mockResolvedValue({ data: {} })
    const wrapper = await mountPanel()
    // 点编辑
    const editBtn = wrapper.findAll('button').find((b) => b.text() === '编辑')
    await editBtn.trigger('click')
    await flushPromises()
    // 清空标题并保存
    const titleInput = wrapper.find('.stub-input')
    await titleInput.setValue('')
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    await saveBtn.trigger('click')
    await flushPromises()
    // title 应为空串（而非 null）——后端 null = 保持原值，空串 = 置 NULL
    expect(updateMemory).toHaveBeenCalledWith(1, '42', expect.objectContaining({
      title: '',
      content: '原文',
      type: 'fact',
    }))
  })

  it('编辑时内容清空拒绝提交', async () => {
    const data = [{ id: '1', type: 'fact', content: 'x', title: 'T', enabled: true }]
    mocks.api.listMemories.mockResolvedValue({ data, headers: {} })
    const wrapper = await mountPanel()
    const editBtn = wrapper.findAll('button').find((b) => b.text() === '编辑')
    await editBtn.trigger('click')
    await flushPromises()
    const textarea = wrapper.find('.stub-textarea')
    await textarea.setValue('')
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    await saveBtn.trigger('click')
    expect(message.warning).toHaveBeenCalledWith('内容不能为空')
    expect(updateMemory).not.toHaveBeenCalled()
  })
})
