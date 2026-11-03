// @vitest-environment happy-dom
// 变更记录详情整页的地址层 + 装配层：地址 → 拉一条变更 → 交给双栏面板。
// 行模型与展开行为在 ChangeDiffPane.test.js 钉；这里只钉本页独有的四件事：
// 地址解析与「非法 id 一个请求都不发」、接口失败要说得清、顶栏与状态栏的口径、主题与「在编辑器中打开」。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  route: { params: {}, query: {} },
  getFileChange: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))
vi.mock('../api', () => ({ getFileChange: mocks.getFileChange }))
vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

import FileChangeDetailView from './FileChangeDetailView.vue'
import ChangeDiffPane from '../components/change/ChangeDiffPane.vue'

const CHANGE = {
  id: '345',
  path: 'src/App.vue',
  action: 'edit',
  diff: '--- a/src/App.vue\n+++ b/src/App.vue\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n',
  before_content: 'a\nb\nc\n',
  after_content: 'a\nB\nc\n',
  created_at: '2026-11-02T10:00:00+00:00',
  workspace_id: '12',
  workspace_name: '订单台账',
}

async function mountView({ sessionId = '12', changeId = '345' } = {}) {
  mocks.route.params = { sessionId, changeId }
  const wrapper = mount(FileChangeDetailView)
  await flush()
  return wrapper
}

// 详情是一次 await 之后才落到 DOM 的，等微任务队列跑干净再继续断言
async function flush() {
  for (let i = 0; i < 4; i += 1) await Promise.resolve()
}

beforeEach(() => {
  mocks.getFileChange.mockReset().mockResolvedValue({ data: { ...CHANGE } })
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FileChangeDetailView 地址与取数', () => {
  it('按地址里的会话与记录拉详情，并把行模型交给双栏面板', async () => {
    const wrapper = await mountView()

    expect(mocks.getFileChange).toHaveBeenCalledWith('12', '345')
    const pane = wrapper.findComponent(ChangeDiffPane)
    expect(pane.exists()).toBe(true)
    expect(pane.props('model').status).toBe('ok')
    expect(pane.props('before')).toBe('a\nb\nc\n')
    expect(pane.props('after')).toBe('a\nB\nc\n')
  })

  it('边界：id 不合法（非数字 / 空 / 小数 / 负数）时一个请求都不发，只出错误说明', async () => {
    for (const changeId of ['abc', '', '1.5', '-7', '../9']) {
      const wrapper = await mountView({ changeId })
      expect(mocks.getFileChange).not.toHaveBeenCalled()
      expect(wrapper.find('.zc-fcd-bad').exists()).toBe(true)
    }
  })

  it('边界：会话 id 不合法同样挡住（详情接口挂在会话下，归属校验靠它）', async () => {
    const wrapper = await mountView({ sessionId: 'abc' })

    expect(mocks.getFileChange).not.toHaveBeenCalled()
    expect(wrapper.find('.zc-fcd-bad').exists()).toBe(true)
  })

  it('接口失败（记录不在这个会话下、被别人会话的 id、已删）时说得清，不白屏', async () => {
    mocks.getFileChange.mockRejectedValue(new Error('404'))

    const wrapper = await mountView()

    expect(wrapper.find('.zc-fcd-error').exists()).toBe(true)
    expect(wrapper.find('.zc-fcd-error').text()).toContain('看不到这条变更')
    expect(wrapper.findComponent(ChangeDiffPane).exists()).toBe(false)
  })
})

describe('FileChangeDetailView 顶栏与状态栏', () => {
  it('顶栏给出动作、完整路径与工作区名，路径只当文本', async () => {
    const wrapper = await mountView()
    const head = wrapper.find('.zc-fcd-head')

    expect(head.text()).toContain('修改')
    expect(head.text()).toContain('src/App.vue')
    expect(head.text()).toContain('订单台账')
    expect(head.find('b').exists()).toBe(false)
  })

  it('边界：路径里带尖括号也只当文本，不注入 HTML', async () => {
    mocks.getFileChange.mockResolvedValue({ data: { ...CHANGE, path: '<img src=x>' } })

    const wrapper = await mountView()

    expect(wrapper.find('.zc-fcd-head').text()).toContain('<img src=x>')
    expect(wrapper.find('.zc-fcd-head img').exists()).toBe(false)
  })

  it('状态栏报本次的行数（+n −m），与行模型同源', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('.zc-fcd-status').text()).toContain('+1')
    expect(wrapper.find('.zc-fcd-status').text()).toContain('-1')
  })

  it('新建与删除在顶栏说的是人话（新建/删除，不是 create/delete）', async () => {
    mocks.getFileChange.mockResolvedValue({ data: { ...CHANGE, action: 'create' } })
    const created = await mountView()
    expect(created.find('.zc-fcd-head').text()).toContain('新建')

    mocks.getFileChange.mockResolvedValue({ data: { ...CHANGE, action: 'delete' } })
    const deleted = await mountView()
    expect(deleted.find('.zc-fcd-head').text()).toContain('删除')
  })
})

describe('FileChangeDetailView 主题与跳转', () => {
  it('默认深色，点主题按钮切到浅色并记在本机', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('.zc-fcd').attributes('data-theme')).toBe('dark')

    await wrapper.find('.zc-fcd-theme-toggle').trigger('click')

    expect(wrapper.find('.zc-fcd').attributes('data-theme')).toBe('light')
    expect(localStorage.getItem('zc.workbench.theme.v1')).toBe('light')
  })

  it('「在编辑器中打开该文件」按工作区开文件管理器整页，并带上这条变更的路径', async () => {
    const open = vi.fn(() => ({}))
    vi.stubGlobal('open', open)

    const wrapper = await mountView()
    await wrapper.find('.zc-fcd-open-editor').trigger('click')

    expect(open).toHaveBeenCalledTimes(1)
    const [url, name] = open.mock.calls[0]
    // 名字一并带上：工作台顶栏本来就从 query 读名字，不为一个标题再请求一次工作区
    expect(url).toBe('/workspaces/12/files?name=%E8%AE%A2%E5%8D%95%E5%8F%B0%E8%B4%A6&file=src%2FApp.vue')
    expect(name).toBe('zerocode-files-12')
  })

  it('被弹窗拦截时如实提示，不让用户以为已经打开', async () => {
    vi.stubGlobal('open', vi.fn(() => null))
    const error = (await import('ant-design-vue')).message.error

    const wrapper = await mountView()
    await wrapper.find('.zc-fcd-open-editor').trigger('click')

    expect(error).toHaveBeenCalled()
  })
})
