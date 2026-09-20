// @vitest-environment happy-dom
// FilePanel 工具条「打开文件夹」入口的行为契约：
//   位置在「刷新」右侧；auto 按访问地址分流；直开失败必须降级到内置文件管理器（不能只弹错误了之）。
// mock 掉 api 层与 antd message，专注验证分流本身。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  // FilePanel 与 WorkspaceExplorer 直接使用
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

import { tree, openFolder, listDir, getConfig } from '../api'
import { message } from 'ant-design-vue'
import FilePanel from './FilePanel.vue'
import WorkspaceExplorer from './WorkspaceExplorer.vue'

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
const STUBS = {
  'a-layout-sider': slotDiv('a-layout-sider'),
  'a-tabs': slotDiv('a-tabs'),
  'a-tab-pane': slotDiv('a-tab-pane'),
  'a-button': ButtonStub,
  'a-spin': slotDiv('a-spin'),
  'a-empty': slotDiv('a-empty'),
  'a-tree': slotDiv('a-tree'),
  'a-alert': slotDiv('a-alert'),
  'a-textarea': slotDiv('a-textarea'),
  'a-modal': slotDiv('a-modal'),
  'a-table': slotDiv('a-table'),
  'a-breadcrumb': slotDiv('a-breadcrumb'),
  'a-tooltip': slotDiv('a-tooltip'),
  DiffBlock: true,
}

// mode 走真实链路：ensureLoaded 从 /api/config 取值，故桩上 fs.open_folder_mode
async function mountPanel(mode = 'auto') {
  const pinia = createPinia()
  setActivePinia(pinia)
  getConfig.mockResolvedValue({ data: { 'fs.open_folder_mode': mode } })
  tree.mockResolvedValue({ data: [] })
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

  it('auto + 本机访问：调起系统资源管理器，不开内置管理器', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    openFolder.mockResolvedValue({ data: { ok: true, path: 'D:/ws' } })
    const wrapper = await mountPanel('auto')

    await clickOpen(wrapper)

    expect(openFolder).toHaveBeenCalledWith(7)
    expect(wrapper.findComponent(WorkspaceExplorer).props('open')).toBe(false)
  })

  it('auto + 远端访问（局域网 / 远端桌面）：走内置文件管理器，完全不请求直开', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    const wrapper = await mountPanel('auto')

    await clickOpen(wrapper)

    expect(openFolder).not.toHaveBeenCalled()
    expect(wrapper.findComponent(WorkspaceExplorer).props('open')).toBe(true)
  })

  it('直开失败：降级打开内置文件管理器并说明原因', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    openFolder.mockRejectedValue({ response: { data: { detail: '打开文件夹失败：找不到 xdg-open' } } })
    const wrapper = await mountPanel('auto')

    await clickOpen(wrapper)

    expect(wrapper.findComponent(WorkspaceExplorer).props('open')).toBe(true)
    expect(message.info).toHaveBeenCalled()
  })

  it('系统参数强制 web：本机访问也用内置文件管理器', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    const wrapper = await mountPanel('web')

    await clickOpen(wrapper)

    expect(openFolder).not.toHaveBeenCalled()
    expect(wrapper.findComponent(WorkspaceExplorer).props('open')).toBe(true)
  })

  it('系统参数强制 system：远端访问也照请求直开（人工覆盖优先于地址判定）', async () => {
    vi.stubGlobal('location', { hostname: '10.1.2.3' })
    openFolder.mockResolvedValue({ data: { ok: true } })
    const wrapper = await mountPanel('system')

    await clickOpen(wrapper)

    expect(openFolder).toHaveBeenCalledWith(7)
  })

  it('切换工作区时关掉内置文件管理器（别让新工作区接着显示旧目录）', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    const wrapper = await mountPanel('auto')
    await clickOpen(wrapper)
    expect(wrapper.findComponent(WorkspaceExplorer).props('open')).toBe(true)

    await wrapper.setProps({ workspaceId: 8 })
    await flushPromises()

    expect(wrapper.findComponent(WorkspaceExplorer).props('open')).toBe(false)
  })
})
