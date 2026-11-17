// @vitest-environment happy-dom
// 工作台整页的地址层：/workspaces/:id/files 只管「地址 → 该不该挂工作台」。
// 树的懒加载与页签行为在 WorkspaceWorkbench.test.js 里钉；这里只钉 id/name/file 三个参数的解析与透传：
// id 原样当字符串、name 取首值且只当文本、file 取首值、非法 id 一个工作台都不挂。
// 工作台组件桩掉（它内部会拉 monaco），不建真 router（守则与守卫与本用例无关）。
// 角色来自 GET /workspaces（Ticket 005）：本页必须自己取，直链刷新不依赖入口带参数。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({ route: { params: {}, query: {} } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))
vi.mock('../api', () => ({ listWorkspaces: vi.fn() }))
vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
vi.mock('../components/WorkspaceWorkbench.vue', async () => {
  const { h } = await import('vue')
  return {
    default: {
      name: 'WorkspaceWorkbench',
      props: ['workspaceId', 'workspaceName', 'initialFile', 'workspaceReadOnly'],
      render() {
        return h('div', {
          class: 'stub-workbench',
          'data-id': String(this.workspaceId),
          'data-name': this.workspaceName,
          'data-file': this.initialFile,
          'data-read-only': String(!!this.workspaceReadOnly),
        })
      },
    },
  }
})

import { listWorkspaces } from '../api'
import WorkspaceWorkbench from '../components/WorkspaceWorkbench.vue'
import WorkspaceFilesView from './WorkspaceFilesView.vue'

const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})
const STUBS = {
  'a-alert': slotDiv('a-alert'),
}

async function mountView({ id, query = {} } = {}) {
  mocks.route.params = id === undefined ? {} : { id }
  mocks.route.query = query
  const wrapper = mount(WorkspaceFilesView, { global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  // 默认：列出的两个 id 都是自有工作区（既有用例不关心角色）
  listWorkspaces.mockResolvedValue({
    data: [
      { id: 7, name: '订单台账', role: 'owner' },
      { id: '2026092201000900123', role: 'owner' },
    ],
  })
})

describe('WorkspaceFilesView 整页直链', () => {
  it('按地址里的 id 挂工作台，id 原样按字符串透传（19 位大整数不做 Number 转换）', async () => {
    const wrapper = await mountView({ id: '2026092201000900123' })

    const wb = wrapper.findComponent(WorkspaceWorkbench)
    expect(wb.exists()).toBe(true)
    expect(wb.props('workspaceId')).toBe('2026092201000900123')
  })

  it('页头用 ?name= 带过来的工作区名（入口知道名字，页面不必再请求一次）', async () => {
    const wrapper = await mountView({ id: '7', query: { name: '订单台账' } })

    expect(wrapper.find('.zc-wsv-head').text()).toContain('订单台账')
    expect(wrapper.findComponent(WorkspaceWorkbench).props('workspaceName')).toBe('订单台账')
  })

  it('边界：没带名字时兜底显示 #id，不露出 undefined 空标题', async () => {
    const wrapper = await mountView({ id: '7' })

    expect(wrapper.find('.zc-wsv-head').text()).toContain('#7')
    expect(wrapper.find('.zc-wsv-head').text()).not.toContain('undefined')
    expect(wrapper.findComponent(WorkspaceWorkbench).props('workspaceName')).toBe('')
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

  it('?file= 原样交给工作台；多值时取第一个', async () => {
    const one = await mountView({ id: '7', query: { file: 'docs/guide.md' } })
    expect(one.findComponent(WorkspaceWorkbench).props('initialFile')).toBe('docs/guide.md')

    const many = await mountView({ id: '7', query: { file: ['a.md', 'b.md'] } })
    expect(many.findComponent(WorkspaceWorkbench).props('initialFile')).toBe('a.md')
  })

  it('边界：没带 ?file= 时交给工作台空串，不出现 undefined', async () => {
    const wrapper = await mountView({ id: '7' })

    expect(wrapper.findComponent(WorkspaceWorkbench).props('initialFile')).toBe('')
  })

  it('边界：id 不合法（非数字 / 空 / 小数 / 负数 / 带路径）时不挂工作台，只出错误说明', async () => {
    for (const id of ['abc', '', '1.5', '-7', '../8', '7/files']) {
      const wrapper = await mountView({ id })
      expect(wrapper.findComponent(WorkspaceWorkbench).exists()).toBe(false)
      expect(wrapper.find('.zc-wsv-bad').exists()).toBe(true)
    }
  })

  it('边界：地址里压根没有 id 段（params 为空）同样只出错误说明', async () => {
    const wrapper = await mountView()

    expect(wrapper.findComponent(WorkspaceWorkbench).exists()).toBe(false)
    expect(wrapper.find('.zc-wsv-bad').exists()).toBe(true)
  })

  it('非法 id 一个列表请求都不发（角色判不了就别惊动后端）', async () => {
    await mountView({ id: 'abc' })

    expect(listWorkspaces).not.toHaveBeenCalled()
  })
})

describe('WorkspaceFilesView 角色与只读（Ticket 005）', () => {
  it('被共享者直达页面：角色来自服务端返回的 role，工作台整页只读', async () => {
    listWorkspaces.mockResolvedValue({ data: [{ id: 7, role: 'collaborator' }] })

    const wrapper = await mountView({ id: '7' })

    expect(listWorkspaces).toHaveBeenCalledTimes(1)
    const wb = wrapper.findComponent(WorkspaceWorkbench)
    expect(wb.props('workspaceReadOnly')).toBe(true)
    expect(wrapper.find('.zc-wsv-head').text()).toContain('只读')
    expect(wrapper.find('.zc-wsv-head').text()).toContain('被共享')
  })

  it('所有者保持可写：自有工作区不带只读开关', async () => {
    const wrapper = await mountView({ id: '7' })

    expect(wrapper.findComponent(WorkspaceWorkbench).props('workspaceReadOnly')).toBe(false)
    expect(wrapper.find('.zc-wsv-ro-tag').exists()).toBe(false)
  })

  it('列表里没有这个工作区（他人 / 已撤销）：不挂工作台，只出错误说明', async () => {
    listWorkspaces.mockResolvedValue({ data: [] })

    const wrapper = await mountView({ id: '7' })

    expect(wrapper.findComponent(WorkspaceWorkbench).exists()).toBe(false)
    expect(wrapper.find('.zc-wsv-bad').attributes('message')).toContain('不存在或无权访问')
  })

  it('边界：列表请求失败同样停在错误态，不猜角色硬挂工作台', async () => {
    listWorkspaces.mockRejectedValue(new Error('network'))

    const wrapper = await mountView({ id: '7' })

    expect(wrapper.findComponent(WorkspaceWorkbench).exists()).toBe(false)
    expect(wrapper.find('.zc-wsv-bad').exists()).toBe(true)
  })
})
