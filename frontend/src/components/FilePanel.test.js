// @vitest-environment happy-dom
// FilePanel 工具条「打开文件夹」入口的行为契约：
//   位置在「刷新」右侧；auto 按访问地址分流；直开失败必须降级到内置文件管理器（不能只弹错误了之）。
//   内置那一路现在是新标签打开整页，所以这里桩掉 window.open，断言拼出来的地址。
// mock 掉 api 层与 antd message，专注验证分流本身。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  // FilePanel 直接使用（整页化后本组件不再挂 WorkspaceExplorer，listDir 只是历史遗留桩）
  tree: vi.fn(), readFile: vi.fn(), saveFile: vi.fn(), getFileChanges: vi.fn(),
  listDir: vi.fn(), openFolder: vi.fn(),
  // chat / config store 会经 api 层拉数据
  listWorkspaces: vi.fn(), getMessages: vi.fn(), getSessionDetail: vi.fn(),
  listModels: vi.fn(), patchSessionModel: vi.fn(), getConfig: vi.fn(), putConfig: vi.fn(),
  wsUrl: vi.fn(() => 'ws://t'),
}))
vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

import { tree, openFolder, listDir, getConfig, readFile, saveFile } from '../api'
import { message } from 'ant-design-vue'
import FilePanel from './FilePanel.vue'
import { useChatStore } from '../stores/chat'

// 只渲染默认插槽的桩：让工具条与按钮在 happy-dom 下真实存在（无编译器，故用 h 而非 template）
const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})
const ButtonStub = {
  name: 'a-button',
  render() {
    return h('button', { class: 'stub-btn', onClick: (e) => this.$emit('click', e) },
      this.$slots.default ? this.$slots.default() : [])
  },
}
const AlertStub = {
  name: 'a-alert',
  props: ['message'],
  render() {
    return h('div', { class: 'stub-a-alert' }, this.message)
  },
}
const STUBS = {
  'a-layout-sider': slotDiv('a-layout-sider'),
  'a-tabs': slotDiv('a-tabs'),
  'a-tab-pane': slotDiv('a-tab-pane'),
  'a-button': ButtonStub,
  'a-spin': slotDiv('a-spin'),
  'a-empty': slotDiv('a-empty'),
  'a-tree': slotDiv('a-tree'),
  'a-alert': AlertStub,
  'a-textarea': slotDiv('a-textarea'),
  'a-modal': slotDiv('a-modal'),
  'a-table': slotDiv('a-table'),
  'a-breadcrumb': slotDiv('a-breadcrumb'),
  'a-tooltip': slotDiv('a-tooltip'),
  DiffBlock: true,
}

// mode 走真实链路：ensureLoaded 从 /api/config 取值，故桩上 fs.open_folder_mode
async function mountPanel(mode = 'auto', nodes = []) {
  const pinia = createPinia()
  setActivePinia(pinia)
  getConfig.mockResolvedValue({ data: { 'fs.open_folder_mode': mode } })
  tree.mockResolvedValue({ data: nodes })
  listDir.mockResolvedValue({ data: { path: '', parent: null, entries: [] } })
  const wrapper = mount(FilePanel, {
    props: { workspaceId: 7 },
    global: { plugins: [pinia], stubs: STUBS },
  })
  await flushPromises()
  return wrapper
}

// 「文件」tab 工具条里的第二个按钮就是「打开文件夹」
async function clickOpen(wrapper) {
  await wrapper.findAll('.zc-fp-toolbar')[0].findAll('button')[1].trigger('click')
  await flushPromises()
}

// 新标签整页的可观測替身：ret 默认返回一个窗口对象（未被拦截）
function stubOpen(ret = {}) {
  const open = vi.fn(() => ret)
  vi.stubGlobal('open', open)
  return open
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FilePanel 打开文件夹入口', () => {
  it('按钮就在「刷新」右侧', async () => {
    const wrapper = await mountPanel()
    const labels = wrapper.findAll('.zc-fp-toolbar')[0].findAll('button').map((b) => b.text().trim())
    expect(labels).toEqual(['刷新', '打开文件夹'])
  })

  it('auto + 本机访问：调起系统资源管理器，不碰 window.open', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    const open = stubOpen()
    openFolder.mockResolvedValue({ data: { ok: true, path: 'D:/ws' } })
    const wrapper = await mountPanel('auto')

    await clickOpen(wrapper)

    expect(openFolder).toHaveBeenCalledWith(7)
    expect(open).not.toHaveBeenCalled()
  })

  it('auto + 远端访问（局域网 / 远端桌面）：新标签打开整页，完全不请求直开', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    const open = stubOpen()
    const wrapper = await mountPanel('auto')

    await clickOpen(wrapper)

    expect(openFolder).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledWith('/workspaces/7/files', 'zerocode-files-7')
  })

  it('直开失败：降级新标签打开整页并说明原因', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    const open = stubOpen()
    openFolder.mockRejectedValue({ response: { data: { detail: '打开文件夹失败：找不到 xdg-open' } } })
    const wrapper = await mountPanel('auto')

    await clickOpen(wrapper)

    expect(open).toHaveBeenCalledWith('/workspaces/7/files', 'zerocode-files-7')
    expect(message.info).toHaveBeenCalled()
  })

  it('系统参数强制 web：本机访问也用内置文件管理器整页', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    const open = stubOpen()
    const wrapper = await mountPanel('web')

    await clickOpen(wrapper)

    expect(openFolder).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledWith('/workspaces/7/files', 'zerocode-files-7')
  })

  it('系统参数强制 system：远端访问也照请求直开（人工覆盖优先于地址判定）', async () => {
    vi.stubGlobal('location', { hostname: '10.1.2.3' })
    openFolder.mockResolvedValue({ data: { ok: true } })
    const wrapper = await mountPanel('system')

    await clickOpen(wrapper)

    expect(openFolder).toHaveBeenCalledWith(7)
  })

  it('边界：地址带上工作区名，整页顶栏不必再请求一次才知道标题', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    const open = stubOpen()
    const wrapper = await mountPanel('auto')
    useChatStore().workspaces = [{ id: 7, name: '订单台账' }]

    await clickOpen(wrapper)

    const url = open.mock.calls[0][0]
    expect(url.startsWith('/workspaces/7/files?name=')).toBe(true)
    expect(decodeURIComponent(url.split('?name=')[1])).toBe('订单台账')
  })

  it('边界：新标签被浏览器拦截（open 返回 null）时给出可操作的提示，不静默失败', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    stubOpen(null)
    const wrapper = await mountPanel('auto')

    await clickOpen(wrapper)

    expect(message.warning).toHaveBeenCalled()
  })

  it('边界：没加载到工作区名时地址是干净的，不带 ?name=', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    const open = stubOpen()
    const wrapper = await mountPanel('auto')

    await clickOpen(wrapper)

    expect(open.mock.calls[0][0]).toBe('/workspaces/7/files')
    expect(message.warning).not.toHaveBeenCalled()
  })
})

describe('FilePanel 只读预览与在编辑器中打开', () => {
  const NODES = [
    { name: 'src', path: 'src', type: 'dir', children: [{ name: 'a.md', path: 'src/a.md', type: 'file' }] },
    { name: 'broken.bin', path: 'broken.bin', type: 'file' },
  ]

  async function selectFile(wrapper, path) {
    await wrapper.vm.onSelect([path])
    await flushPromises()
  }

  it('选中可读文件只展示只读预览：没有编辑/保存/取消，也不调用 saveFile', async () => {
    readFile.mockResolvedValue({ data: { content: '# 标题' } })
    const wrapper = await mountPanel('auto', NODES)

    await selectFile(wrapper, 'src/a.md')

    expect(wrapper.find('.zc-fp-content').text()).toContain('# 标题')
    expect(wrapper.find('.zc-fp-editor').exists()).toBe(false)
    const labels = wrapper.findAll('button').map((b) => b.text().trim())
    expect(labels).not.toContain('编辑')
    expect(labels).not.toContain('保存')
    expect(labels).not.toContain('取消')
    expect(saveFile).not.toHaveBeenCalled()
    expect(readFile).toHaveBeenCalledWith(7, 'src/a.md')
  })

  it('选中可读文件出现「在编辑器中打开」，新标签地址带 name 与 file 且都编码', async () => {
    const open = stubOpen()
    readFile.mockResolvedValue({ data: { content: 'x' } })
    const wrapper = await mountPanel('auto', NODES)
    useChatStore().workspaces = [{ id: 7, name: '订单 台账' }]

    await selectFile(wrapper, 'src/a.md')
    await wrapper.find('.zc-fp-open-editor').trigger('click')

    expect(open).toHaveBeenCalledWith(
      '/workspaces/7/files?name=%E8%AE%A2%E5%8D%95%20%E5%8F%B0%E8%B4%A6&file=src%2Fa.md',
      'zerocode-files-7',
    )
    expect(message.warning).not.toHaveBeenCalled()
  })

  it('「在编辑器中打开」的新标签被拦截时给可操作提示，不静默失败', async () => {
    stubOpen(null)
    readFile.mockResolvedValue({ data: { content: 'x' } })
    const wrapper = await mountPanel('auto', NODES)

    await selectFile(wrapper, 'src/a.md')
    await wrapper.find('.zc-fp-open-editor').trigger('click')

    expect(message.warning).toHaveBeenCalled()
  })

  it('读取失败（二进制/超限）照旧展示 detail，且不出现「在编辑器中打开」', async () => {
    readFile.mockRejectedValue({ response: { data: { detail: '不支持读取的文件（二进制或超过200KB）' } } })
    const wrapper = await mountPanel('auto', NODES)

    await selectFile(wrapper, 'broken.bin')

    expect(wrapper.find('.stub-a-alert').text()).toContain('不支持读取的文件')
    expect(wrapper.find('.zc-fp-open-editor').exists()).toBe(false)
  })

  it('切工作区清空预览与「在编辑器中打开」', async () => {
    readFile.mockResolvedValue({ data: { content: '# 标题' } })
    const wrapper = await mountPanel('auto', NODES)
    await selectFile(wrapper, 'src/a.md')

    await wrapper.setProps({ workspaceId: 8 })
    await flushPromises()

    expect(wrapper.find('.zc-fp-preview').exists()).toBe(false)
    expect(wrapper.find('.zc-fp-open-editor').exists()).toBe(false)
  })
})
