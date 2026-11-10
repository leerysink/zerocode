// @vitest-environment happy-dom
// 记忆详情整页的地址层 + 装配层：地址 → 拉列表定位 → 左栏切换 → 启用/编辑/删除。
// 左栏渲染在 MemoryItemList.test.js 钉；这里只钉本页独有的行为：
// 地址闸门、切条与地址同步、行内回写（不重复拉列表）、删后切邻条/删空、截断与状态条。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  route: { params: {} },
  replace: vi.fn(),
  api: {
    listMemories: vi.fn(),
    updateMemory: vi.fn(),
    deleteMemory: vi.fn(),
  },
  antd: {
    message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    Modal: { confirm: vi.fn() },
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ replace: mocks.replace }),
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

import MemoryDetailView from './MemoryDetailView.vue'
import MemoryItemList from '../components/memory/MemoryItemList.vue'

const ROWS = [
  {
    id: '12', type: 'fact', title: '部署域名', content: 'prod 走网关', enabled: true,
    created_at: '2026-11-01T10:00:00', updated_at: '2026-11-02T09:30:00',
  },
  {
    id: '11', type: 'preference', title: '风格', content: '喜欢深色', enabled: false,
    created_at: '2026-11-01T09:00:00', updated_at: '2026-11-01T09:00:00',
  },
  {
    id: '10', type: 'fact', title: '端口', content: 'dev 用 8100', enabled: true,
    created_at: '2026-10-31T09:00:00', updated_at: '2026-10-31T09:00:00',
  },
]

const STUBS = {
  // 不在这里转一次 click：@click 会作为原生监听器落到根元素上，再转一次就是双触发
  'a-button': {
    name: 'a-button',
    props: ['size', 'type', 'danger', 'loading'],
    render() {
      return h('button', { class: 'stub-btn' }, this.$slots.default?.())
    },
  },
  'a-switch': {
    name: 'a-switch',
    props: ['checked', 'size', 'disabled'],
    render() {
      return h('input', {
        class: 'stub-switch', type: 'checkbox', checked: this.checked,
        onChange: () => this.$emit('change'),
      })
    },
  },
  'a-select': {
    name: 'a-select',
    props: ['value', 'size'],
    render() {
      return h('select', {
        class: 'stub-select', value: this.value,
        onChange: (e) => this.$emit('update:value', e.target.value),
      }, this.$slots.default?.())
    },
  },
  'a-select-option': {
    name: 'a-select-option',
    props: ['value'],
    render() { return h('option', { value: this.value }, this.$slots.default?.()) },
  },
  'a-input': {
    name: 'a-input',
    props: ['value', 'placeholder', 'size'],
    render() {
      return h('input', {
        class: 'stub-input', value: this.value,
        onInput: (e) => this.$emit('update:value', e.target.value),
      })
    },
  },
  'a-textarea': {
    name: 'a-textarea',
    props: ['value', 'rows'],
    render() {
      return h('textarea', {
        class: 'stub-textarea', value: this.value,
        onInput: (e) => this.$emit('update:value', e.target.value),
      })
    },
  },
}

async function mountView({ id = '933', memoryId = '12' } = {}) {
  mocks.route.params = { id, memoryId }
  const wrapper = mount(MemoryDetailView, { global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

function clickText(wrapper, text) {
  const btn = wrapper.findAll('.stub-btn').find((b) => b.text() === text)
  if (!btn) throw new Error(`button not found: ${text}`)
  return btn.trigger('click')
}

beforeEach(() => {
  mocks.route.params = {}
  mocks.replace.mockReset()
  mocks.api.listMemories.mockReset().mockResolvedValue({ data: ROWS.map((r) => ({ ...r })), headers: {} })
  mocks.api.updateMemory.mockReset()
  mocks.api.deleteMemory.mockReset()
  mocks.antd.message.success.mockReset()
  mocks.antd.message.error.mockReset()
  mocks.antd.message.warning.mockReset()
  mocks.antd.message.info.mockReset()
  mocks.antd.Modal.confirm.mockReset()
  localStorage.clear()
})

afterEach(() => vi.clearAllMocks())

describe('MemoryDetailView 地址与取数', () => {
  it('地址里任一段不是数字：一个请求都不发，给出可操作的提示', async () => {
    for (const bad of [{ id: 'abc', memoryId: '12' }, { id: '933', memoryId: 'x' }]) {
      mocks.api.listMemories.mockClear()
      const wrapper = await mountView(bad)

      expect(mocks.api.listMemories).not.toHaveBeenCalled()
      expect(wrapper.find('.zc-memd-bad').exists()).toBe(true)
    }
  })

  it('正常地址：按工作区拉一次列表，当前条与全文就位', async () => {
    const wrapper = await mountView()

    expect(mocks.api.listMemories).toHaveBeenCalledWith('933')
    expect(wrapper.findComponent(MemoryItemList).exists()).toBe(true)
    expect(wrapper.findAll('.zc-mil-item')).toHaveLength(3)
    expect(wrapper.find('.zc-memd-name').text()).toBe('部署域名')
    expect(wrapper.find('.zc-memd-content').text()).toBe('prod 走网关')
    expect(wrapper.find('.zc-memd-status').text()).toContain('共 3 条')
    expect(wrapper.find('.zc-memd-status').text()).toContain('启用')
    expect(wrapper.find('.zc-memd-status').text()).toContain('2026-11-02 09:30')
  })

  it('当前记忆不在列表里：右栏说清「已不存在」，左栏仍可用', async () => {
    const wrapper = await mountView({ memoryId: '99' })

    expect(wrapper.find('.zc-memd-note').text()).toContain('该记忆已不存在')
    expect(wrapper.findComponent(MemoryItemList).exists()).toBe(true)
  })

  it('列表失败：右侧给提示，左栏重试成功后补出内容', async () => {
    mocks.api.listMemories.mockRejectedValueOnce(new Error('500'))

    const wrapper = await mountView()
    expect(wrapper.find('.zc-memd-note').text()).toContain('加载失败')

    await wrapper.find('.zc-mil-retry').trigger('click')
    await flushPromises()

    expect(mocks.api.listMemories).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.zc-memd-content').text()).toBe('prod 走网关')
  })

  it('截断响应头落到状态条上，不静默少给', async () => {
    mocks.api.listMemories.mockResolvedValue({
      data: ROWS.map((r) => ({ ...r })),
      headers: { 'x-memory-truncated': 'true', 'x-memory-limit': '200' },
    })
    const wrapper = await mountView()

    expect(wrapper.find('.zc-memd-status').text()).toContain('仅展示最近 200 条')
  })
})

describe('MemoryDetailView 左栏切换', () => {
  it('点另一条：同页换内容并把地址同步成新记忆，不重复拉列表', async () => {
    const wrapper = await mountView()
    expect(mocks.api.listMemories).toHaveBeenCalledTimes(1)

    await wrapper.findAll('.zc-mil-item')[2].trigger('click')
    await flushPromises()

    expect(mocks.replace).toHaveBeenCalledWith({
      name: 'workspace-memory-detail',
      params: { id: '933', memoryId: '10' },
    })
    expect(wrapper.find('.zc-memd-name').text()).toBe('端口')
    expect(wrapper.find('.zc-memd-content').text()).toBe('dev 用 8100')
    expect(mocks.api.listMemories).toHaveBeenCalledTimes(1)
  })

  it('点当前这条不重复导航，也不重写地址', async () => {
    const wrapper = await mountView()

    await wrapper.findAll('.zc-mil-item')[1].trigger('click')
    await flushPromises()

    expect(mocks.replace).not.toHaveBeenCalled()
  })
})

describe('MemoryDetailView 启用与编辑', () => {
  it('翻转开关以服务端返回为准写回当前行', async () => {
    mocks.api.updateMemory.mockResolvedValue({ data: { ...ROWS[0], enabled: false } })

    const wrapper = await mountView()
    await wrapper.find('.stub-switch').trigger('change')
    await flushPromises()

    expect(mocks.api.updateMemory).toHaveBeenCalledWith('933', '12', { enabled: false })
    expect(wrapper.find('.zc-memd-name').text()).toBe('部署域名')
    expect(wrapper.find('.zc-memd-status').text()).toContain('停用')
    expect(wrapper.findAll('.zc-mil-item')[1].text()).toContain('停用')
  })

  it('编辑保存：带上类型/标题/内容，成功后原地换内容', async () => {
    mocks.api.updateMemory.mockResolvedValue({
      data: { ...ROWS[0], content: 'prod 走网关（已改）', title: '部署域名' },
    })

    const wrapper = await mountView()
    await clickText(wrapper, '编辑')
    await wrapper.find('textarea').setValue('prod 走网关（已改）')
    await clickText(wrapper, '保存')
    await flushPromises()

    expect(mocks.api.updateMemory).toHaveBeenCalledWith('933', '12', {
      type: 'fact', content: 'prod 走网关（已改）', title: '部署域名',
    })
    expect(wrapper.find('.zc-memd-content').text()).toBe('prod 走网关（已改）')
    expect(mocks.antd.message.success).toHaveBeenCalled()
  })

  it('内容留空不提交，只提示', async () => {
    const wrapper = await mountView()
    await clickText(wrapper, '编辑')
    await wrapper.find('textarea').setValue('   ')
    await clickText(wrapper, '保存')

    expect(mocks.api.updateMemory).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalledWith('内容不能为空')
  })
})

describe('MemoryDetailView 删除', () => {
  it('二次确认后删掉当前条，自动切到左栏相邻一条并同步地址', async () => {
    mocks.api.deleteMemory.mockResolvedValue({ data: { ...ROWS[0] } })

    const wrapper = await mountView()
    await clickText(wrapper, '删除')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)

    await mocks.antd.Modal.confirm.mock.calls[0][0].onOk()
    await flushPromises()

    expect(mocks.api.deleteMemory).toHaveBeenCalledWith('933', '12')
    expect(mocks.replace).toHaveBeenCalledWith({
      name: 'workspace-memory-detail',
      params: { id: '933', memoryId: '10' },
    })
    expect(wrapper.find('.zc-memd-name').text()).toBe('端口')
    expect(wrapper.findAll('.zc-mil-item')).toHaveLength(2)
  })

  it('删掉唯一一条：左右都进空态，不再改地址', async () => {
    mocks.api.listMemories.mockResolvedValue({ data: [{ ...ROWS[0] }], headers: {} })
    mocks.api.deleteMemory.mockResolvedValue({ data: { ...ROWS[0] } })

    const wrapper = await mountView()
    await clickText(wrapper, '删除')
    await mocks.antd.Modal.confirm.mock.calls[0][0].onOk()
    await flushPromises()

    expect(mocks.replace).not.toHaveBeenCalled()
    expect(wrapper.find('.zc-memd-note').text()).toContain('暂无记忆')
    expect(wrapper.find('.zc-mil-empty').exists()).toBe(true)
  })
})
