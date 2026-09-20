// @vitest-environment happy-dom
// 内置「类桌面」文件管理器：单层列表来自 GET /api/fs/ls，双击进目录 / 双击预览，下载走原生链接。
// a-table 桩按真实契约渲染：对每个 column 调一次 #bodyCell，并把 customRow 返回的 onDblclick 绑到行上。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../api', () => ({
  listDir: vi.fn(), readFile: vi.fn(), openFolder: vi.fn(),
  tree: vi.fn(), saveFile: vi.fn(), getFileChanges: vi.fn(),
}))
vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

import { listDir, readFile } from '../api'
import WorkspaceExplorer from './WorkspaceExplorer.vue'

const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})
const TableStub = {
  name: 'a-table',
  props: ['dataSource', 'columns', 'customRow', 'rowKey', 'loading', 'size', 'pagination'],
  render() {
    const rows = (this.dataSource || []).map((record, index) =>
      h('div',
        { class: 'stub-row', key: record.path, ...(this.customRow ? this.customRow(record, index) : {}) },
        this.columns.map((c) => h('div', { class: 'stub-cell', 'data-key': c.key },
          this.$slots.bodyCell
            ? this.$slots.bodyCell({ text: record[c.dataIndex], record, index, column: c })
            : [String(record[c.dataIndex] ?? '')]))))
    return h('div', { class: 'stub-table' }, rows)
  },
}
const STUBS = {
  'a-modal': slotDiv('a-modal'),
  'a-button': slotDiv('a-button'),
  'a-table': TableStub,
  'a-spin': slotDiv('a-spin'),
  'a-empty': slotDiv('a-empty'),
  'a-tooltip': slotDiv('a-tooltip'),
}

const dir = (name, path) => ({ name, path, is_dir: true, size: 0, mtime: 1757900000 })
const file = (name, path, size = 12) => ({ name, path, is_dir: false, size, mtime: 1757900000 })
const ls = (path, entries) => {
  const parent = path ? (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '') : null
  return { data: { path, parent, entries } }
}
// 根层统一给「一个目录 src + 一个文件 a.md」，再往下给 inner.txt
const fixture = (ws, path) => Promise.resolve(
  path ? ls(path, [file('inner.txt', `${path}/inner.txt`)]) : ls('', [dir('src', 'src'), file('a.md', 'a.md')]),
)

async function mountExplorer() {
  const wrapper = mount(WorkspaceExplorer, {
    props: { open: true, workspaceId: 7 },
    global: { stubs: STUBS },
  })
  await flushPromises()
  return wrapper
}

const rowNames = (wrapper) => wrapper.findAll('.stub-row').map((r) => r.find('.stub-cell').text())
const cells = (wrapper, i) => wrapper.findAll('.stub-row')[i].findAll('.stub-cell').map((c) => c.text())

beforeEach(() => {
  vi.clearAllMocks()
  listDir.mockImplementation(fixture)
  readFile.mockResolvedValue({ data: { content: '# 标题' } })
})

describe('WorkspaceExplorer', () => {
  it('打开时按工作区根拉单层列表并渲染条目', async () => {
    const wrapper = await mountExplorer()
    expect(listDir).toHaveBeenCalledWith(7, '')
    expect(rowNames(wrapper)).toEqual(['src', 'a.md'])
  })

  it('关闭时不请求目录（不因为挂载就白拉一次）', async () => {
    mount(WorkspaceExplorer, { props: { open: false, workspaceId: 7 }, global: { stubs: STUBS } })
    await flushPromises()
    expect(listDir).not.toHaveBeenCalled()
  })

  it('双击目录进入该层，带相对路径再取', async () => {
    const wrapper = await mountExplorer()
    await wrapper.findAll('.stub-row')[0].trigger('dblclick')
    await flushPromises()

    expect(listDir).toHaveBeenLastCalledWith(7, 'src')
    expect(rowNames(wrapper)).toEqual(['inner.txt'])
  })

  it('双击文件走只读预览，不重新请求目录', async () => {
    const wrapper = await mountExplorer()
    await wrapper.findAll('.stub-row')[1].trigger('dblclick') // a.md
    await flushPromises()

    expect(readFile).toHaveBeenCalledWith(7, 'a.md')
    expect(listDir).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.zc-wex-preview').text()).toContain('# 标题')
  })

  it('预览被后端拒（二进制/超 200KB）时展示原因，不留下 undefined', async () => {
    listDir.mockResolvedValueOnce(ls('', [file('big.bin', 'big.bin', 999999)]))
    const wrapper = await mountExplorer()
    readFile.mockRejectedValue({ response: { data: { detail: '不支持读取的文件（二进制或超过200KB）' } } })

    await wrapper.findAll('.stub-row')[0].trigger('dblclick')
    await flushPromises()

    expect(wrapper.find('.zc-wex-error').text()).toContain('不支持读取的文件')
    expect(wrapper.find('.zc-wex-preview').exists()).toBe(false)
  })

  it('文件行给出下载链接，目录行不给', async () => {
    const wrapper = await mountExplorer()
    expect(wrapper.findAll('.stub-row').map((r) => r.find('.zc-wex-download').exists())).toEqual([false, true])
    expect(wrapper.find('.zc-wex-download').attributes('href'))
      .toBe('/api/fs/download?workspace_id=7&path=a.md')
  })

  it('面包屑回跳根层，上级按钮按当前路径逐级退', async () => {
    const wrapper = await mountExplorer()
    await wrapper.findAll('.stub-row')[0].trigger('dblclick') // 进 src
    await flushPromises()
    expect(wrapper.find('.zc-wex-crumb-current').text()).toBe('src')

    await wrapper.find('.zc-wex-crumb-root').trigger('click')
    await flushPromises()
    expect(listDir).toHaveBeenLastCalledWith(7, '')

    await wrapper.findAll('.stub-row')[0].trigger('dblclick') // 再进 src
    await flushPromises()
    await wrapper.find('.zc-wex-up').trigger('click')
    await flushPromises()
    expect(listDir).toHaveBeenLastCalledWith(7, '')
  })

  it('条目大小与修改时间按桌面惯例展示（目录不给字节数）', async () => {
    const wrapper = await mountExplorer()
    expect(cells(wrapper, 0)[1]).toBe('—')
    expect(cells(wrapper, 1)[1]).toBe('12 B')
    expect(cells(wrapper, 0)[2]).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
  })

  it('刷新按钮按当前所在层重新拉取', async () => {
    const wrapper = await mountExplorer()
    await wrapper.findAll('.stub-row')[0].trigger('dblclick')
    await flushPromises()
    await wrapper.find('.zc-wex-refresh').trigger('click')
    await flushPromises()
    expect(listDir).toHaveBeenCalledTimes(3) // 初次 + 进入 src + 刷新
    expect(listDir).toHaveBeenLastCalledWith(7, 'src')
  })
})
