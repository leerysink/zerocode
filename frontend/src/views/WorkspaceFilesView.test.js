// @vitest-environment happy-dom
// 内置文件管理器的整页（新标签打开 /workspaces/:id/files）：页面只管「地址 → 该不该挂文件管理器」。
// 树与预览的行为在 WorkspaceExplorer.test.js 里钉，这里只钉直链这层：id 原样透传（不做 Number 转换）、
// 地址不合法就一个目录请求都不发、工作区名的兜底与转义。
// vue-router 按 DbAdminView.test.js 的既有做法桩掉 useRoute，不建真 router（守卫与本用例无关）。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({ route: { params: {}, query: {} } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))
vi.mock('../api', () => ({
  listDir: vi.fn(), readFile: vi.fn(), openFolder: vi.fn(),
}))
vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

import { listDir } from '../api'
import WorkspaceFilesView from './WorkspaceFilesView.vue'
import WorkspaceExplorer from '../components/WorkspaceExplorer.vue'

const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})
const STUBS = {
  'a-button': slotDiv('a-button'),
  'a-tree': slotDiv('a-tree'),
  'a-spin': slotDiv('a-spin'),
  'a-empty': slotDiv('a-empty'),
  'a-alert': slotDiv('a-alert'),
  'a-tooltip': slotDiv('a-tooltip'),
}

// 走真实 WorkspaceExplorer（只桩掉它内部的 ant 组件）：这样「不发目录请求」是被真链路验证的
async function mountView({ id, query = {} } = {}) {
  mocks.route.params = id === undefined ? {} : { id }
  mocks.route.query = query
  const wrapper = mount(WorkspaceFilesView, { global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  listDir.mockResolvedValue({ data: { path: '', parent: null, entries: [] } })
})

describe('WorkspaceFilesView 整页直链', () => {
  it('按地址里的 id 拉根层，id 原样按字符串透传（19 位大整数不做 Number 转换）', async () => {
    const wrapper = await mountView({ id: '2026092201000900123' })

    expect(listDir).toHaveBeenCalledTimes(1)
    expect(listDir).toHaveBeenCalledWith('2026092201000900123', '')
    expect(wrapper.findComponent(WorkspaceExplorer).props('workspaceId')).toBe('2026092201000900123')
  })

  it('页头用 ?name= 带过来的工作区名（入口知道名字，页面不必再请求一次）', async () => {
    const wrapper = await mountView({ id: '7', query: { name: '订单台账' } })

    expect(wrapper.find('.zc-wsv-head').text()).toContain('订单台账')
    expect(wrapper.findComponent(WorkspaceExplorer).props('workspaceName')).toBe('订单台账')
  })

  it('边界：没带名字时兜底显示 #id，不露出 undefined 空标题', async () => {
    const wrapper = await mountView({ id: '7' })

    expect(wrapper.find('.zc-wsv-head').text()).toContain('#7')
    expect(wrapper.find('.zc-wsv-head').text()).not.toContain('undefined')
    expect(wrapper.findComponent(WorkspaceExplorer).props('workspaceName')).toBe('')
  })

  it('边界：name 传成数组（手改地址 ?name=a&name=b）取第一个，不渲染成逗号串', async () => {
    const wrapper = await mountView({ id: '7', query: { name: ['甲', '乙'] } })

    expect(wrapper.find('.zc-wsv-head').text()).toContain('甲')
    expect(wrapper.find('.zc-wsv-head').text()).not.toContain('乙')
  })

  it('边界：name 带尖括号也只当文本，不注入 HTML', async () => {
    const wrapper = await mountView({ id: '7', query: { name: '<b>粗</b>' } })

    expect(wrapper.find('.zc-wsv-head').text()).toContain('<b>粗</b>')
    expect(wrapper.find('.zc-wsv-head b').exists()).toBe(false)
  })

  it('边界：id 不合法（非数字 / 空 / 小数 / 负数 / 带路径）时不挂文件管理器、一个目录请求都不发', async () => {
    for (const id of ['abc', '', '1.5', '-7', '../8', '7/files']) {
      await mountView({ id })
      expect(listDir).not.toHaveBeenCalled()
    }

    const wrapper = await mountView({ id: 'abc' })
    expect(wrapper.findComponent(WorkspaceExplorer).exists()).toBe(false)
    expect(wrapper.find('.zc-wsv-bad').exists()).toBe(true)
  })

  it('边界：地址里压根没有 id 段（params 为空）同样只出错误说明', async () => {
    const wrapper = await mountView()

    expect(listDir).not.toHaveBeenCalled()
    expect(wrapper.find('.zc-wsv-bad').exists()).toBe(true)
  })
})
