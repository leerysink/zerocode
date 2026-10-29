// @vitest-environment happy-dom
// 工作台骨架（左树 + 页签 + 编辑区）：树沿用 WorkspaceExplorer 的懒加载契约，页面只钉接线。
// Monaco 一律桩掉（WorkbenchEditor 用本地桩，测试环境不加载 monaco），内容/错误/只读通过 props 断言。
// a-tree 桩照 ant-design-vue 4.x 的懒加载契约：展开时 key 不在 loadedKeys 才调 loadData，
// resolve 后 emit update:loadedKeys；expandAction='click' 时点行 = 先展开再 select，且两者都不判断叶子。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'

vi.mock('../api', () => ({
  listDir: vi.fn(), readFile: vi.fn(), saveFile: vi.fn(),
}))
vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
// 桩掉编辑器容器，避免 monaco（含 ?worker 导入）在 happy-dom 里被求值
vi.mock('./workbench/WorkbenchEditor.vue', async () => {
  const { h } = await import('vue')
  return {
    default: {
      name: 'WorkbenchEditor',
      props: ['workspaceId', 'path', 'content', 'savedContent', 'loading', 'error', 'readOnly', 'theme', 'mode'],
      emits: ['update:content', 'update:dirty', 'save', 'cursor', 'open-file', 'update:mode'],
      render() { return h('div', { class: 'stub-editor' }, this.path || '') },
    },
  }
})
// 图标桩：只把 path/dir 投影到 data-*，宿主测试不关心 SVG 形状（映射由 fileTypes.test.js 覆盖）
vi.mock('./workbench/FileIcon.vue', async () => {
  const { h } = await import('vue')
  return {
    default: {
      name: 'FileIcon',
      props: ['path', 'dir'],
      render() {
        return h('span', {
          class: 'stub-icon',
          'data-path': this.path,
          'data-dir': String(!!this.dir),
        })
      },
    },
  }
})

import { listDir, readFile, saveFile } from '../api'
import { message } from 'ant-design-vue'
import { tabsStorageKey } from '../utils/workbenchTabs'
import { THEME_KEY } from '../utils/theme'
import WorkspaceWorkbench from './WorkspaceWorkbench.vue'

enableAutoUnmount(afterEach)

const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})

const TreeStub = {
  name: 'a-tree',
  props: ['treeData', 'expandedKeys', 'loadedKeys', 'selectedKeys', 'loadData', 'fieldNames', 'expandAction'],
  emits: ['update:expandedKeys', 'expand', 'update:selectedKeys', 'select', 'update:loadedKeys', 'load'],
  methods: {
    flatten(nodes, out) {
      for (const n of nodes || []) {
        out.push(n)
        if (n.children && n.children.length && (this.expandedKeys || []).includes(n.key)) {
          this.flatten(n.children, out)
        }
      }
      return out
    },
    toggle(node) {
      const keys = (this.expandedKeys || []).slice()
      const at = keys.indexOf(node.key)
      if (at >= 0) keys.splice(at, 1)
      else keys.push(node.key)
      this.$emit('update:expandedKeys', keys)
      this.$emit('expand', keys, { node, expanded: at < 0 })
      if (at < 0 && this.loadData && !(this.loadedKeys || []).includes(node.key)) {
        return Promise.resolve(this.loadData(node))
          .then(() => {
            const loaded = (this.loadedKeys || []).concat(node.key)
            this.$emit('update:loadedKeys', loaded)
            this.$emit('load', loaded, { node })
          })
          .catch(() => {})
      }
      return Promise.resolve()
    },
    pick(node) {
      if (this.expandAction === 'click') this.toggle(node)
      const selected = (this.selectedKeys || []).includes(node.key)
      const keys = selected ? [] : [node.key]
      this.$emit('update:selectedKeys', keys)
      this.$emit('select', keys, { node, selected: !selected })
    },
  },
  render() {
    const rows = this.flatten(this.treeData, []).map((n) => h('div', { class: 'stub-tree-node', key: n.key, 'data-key': n.key }, [
      h('span', { class: 'stub-tree-switch', onClick: () => this.toggle(n) }),
      h('span', { class: 'stub-tree-title', onClick: () => this.pick(n) },
        this.$slots.title ? this.$slots.title({ ...n }) : n.title),
    ]))
    return h('div', { class: 'stub-tree' }, rows)
  },
}

const modalDiv = (name) => ({
  name,
  props: ['open', 'title'],
  render() {
    return h('div', { class: `stub-${name}`, 'data-open': String(this.open) }, [
      this.$slots.default ? this.$slots.default() : [],
      this.$slots.footer ? this.$slots.footer() : [],
    ])
  },
})

const STUBS = {
  'a-button': slotDiv('a-button'),
  'a-tree': TreeStub,
  'a-spin': slotDiv('a-spin'),
  'a-empty': slotDiv('a-empty'),
  'a-tooltip': slotDiv('a-tooltip'),
  'a-modal': modalDiv('a-modal'),
}

const dir = (name, path) => ({ name, path, is_dir: true, size: 0, mtime: 1757900000 })
const file = (name, path, size = 12) => ({ name, path, is_dir: false, size, mtime: 1757900000 })
const ls = (path, entries) => ({ data: { path, parent: path || '', entries } })
const LAYERS = {
  '': [dir('src', 'src'), file('a.md', 'a.md')],
  src: [dir('lib', 'src/lib'), file('inner.txt', 'src/inner.txt')],
  'src/lib': [file('deep.txt', 'src/lib/deep.txt')],
}
const fixture = (ws, path) => Promise.resolve(ls(path, LAYERS[path] || []))

async function mountWb(props = {}) {
  const wrapper = mount(WorkspaceWorkbench, {
    props: { workspaceId: 7, workspaceName: '演示工作区', ...props },
    global: { stubs: STUBS },
  })
  await flushPromises()
  return wrapper
}

const titles = (wrapper) => wrapper.findAll('.stub-tree-title').map((t) => t.text())
const treeProps = (wrapper) => wrapper.findComponent({ name: 'a-tree' }).props()
const editor = (wrapper) => wrapper.findComponent({ name: 'WorkbenchEditor' })
const tabTitles = (wrapper) => wrapper.findAll('.zc-wb-tab-title').map((t) => t.text())
const crumbs = (wrapper) => wrapper.findAll('.zc-wb-crumb').map((c) => c.text())
const dirty = (wrapper) => wrapper.findAll('.zc-wb-dirty')
const saveState = (wrapper) => wrapper.find('.zc-wb-save-state').text()

async function markDirty(wrapper, content = '# 改过的内容') {
  editor(wrapper).vm.$emit('update:content', content)
  editor(wrapper).vm.$emit('update:dirty', true)
  await flushPromises()
}
async function pressSave(wrapper) {
  editor(wrapper).vm.$emit('save')
  await flushPromises()
}

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
async function expand(wrapper, key) {
  await wrapper.find(`.stub-tree-node[data-key="${key}"] .stub-tree-switch`).trigger('click')
  await flushPromises()
}
async function select(wrapper, key) {
  await wrapper.find(`.stub-tree-node[data-key="${key}"] .stub-tree-title`).trigger('click')
  await flushPromises()
}
async function closeTab(wrapper, index = 0) {
  await wrapper.findAll('.zc-wb-tab-close')[index].trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  listDir.mockImplementation(fixture)
  readFile.mockImplementation((ws, p) => Promise.resolve({ data: { content: `# ${p}`, mtime_ns: 111 } }))
})

describe('WorkspaceWorkbench 文件树', () => {
  it('进页面拉根层一次，目录与文件都渲染出来', async () => {
    const wrapper = await mountWb()

    expect(listDir).toHaveBeenCalledTimes(1)
    expect(listDir).toHaveBeenCalledWith(7, '')
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
    expect(wrapper.find('.zc-wb-ws-name').text()).toContain('演示工作区')
  })

  it('字符串 id（直链带来的）照常透传，不转 Number', async () => {
    await mountWb({ workspaceId: '2026092201000900123' })

    expect(listDir).toHaveBeenCalledWith('2026092201000900123', '')
  })

  it('展开目录才拉那一层，收起再展开不重拉', async () => {
    const wrapper = await mountWb()

    await expand(wrapper, 'src')
    expect(listDir).toHaveBeenCalledTimes(2)
    expect(listDir).toHaveBeenLastCalledWith(7, 'src')
    expect(titles(wrapper)).toEqual(['src', 'lib', 'inner.txt', 'a.md'])

    await expand(wrapper, 'src')
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
    await expand(wrapper, 'src')

    expect(listDir).toHaveBeenCalledTimes(2)
  })

  it('点目录行就展开，不请求文件内容；点文件行不发 /fs/ls', async () => {
    const wrapper = await mountWb()

    await select(wrapper, 'src')
    expect(treeProps(wrapper).expandedKeys).toEqual(['src'])
    expect(readFile).not.toHaveBeenCalled()

    await select(wrapper, 'src/inner.txt')
    expect(listDir).not.toHaveBeenCalledWith(7, 'src/inner.txt')
    expect(readFile).toHaveBeenCalledWith(7, 'src/inner.txt')
  })

  it('展开失败：提示 detail，这一层不记进 loadedKeys，可重试', async () => {
    listDir.mockImplementation((ws, p) => (p === 'src'
      ? Promise.reject({ response: { data: { detail: '目录不存在或已被删除' } } })
      : fixture(ws, p)))
    const wrapper = await mountWb()

    await expand(wrapper, 'src')

    expect(message.error).toHaveBeenCalledWith('目录不存在或已被删除')
    expect(treeProps(wrapper).expandedKeys).toEqual([])
    expect(treeProps(wrapper).loadedKeys).toEqual([])
    expect(crumbs(wrapper)).toEqual(['演示工作区'])

    listDir.mockImplementation(fixture)
    await expand(wrapper, 'src')
    expect(titles(wrapper)).toContain('inner.txt')
  })

  it('根层为空显示空态', async () => {
    listDir.mockResolvedValueOnce(ls('', []))

    const wrapper = await mountWb()

    expect(wrapper.find('.stub-a-empty').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'a-tree' }).exists()).toBe(false)
  })
})

describe('WorkspaceWorkbench 页签', () => {
  it('点文件开页签并读取内容，页签标题是文件名、hover 是完整路径', async () => {
    const wrapper = await mountWb()

    await select(wrapper, 'a.md')

    expect(readFile).toHaveBeenCalledWith(7, 'a.md')
    expect(tabTitles(wrapper)).toEqual(['a.md'])
    expect(wrapper.find('.zc-wb-tab').attributes('title')).toBe('a.md')
    expect(editor(wrapper).props('path')).toBe('a.md')
    expect(editor(wrapper).props('content')).toBe('# a.md')
    expect(editor(wrapper).props('loading')).toBe(false)
  })

  it('同一文件再点只激活，不新建页签、不重复读取', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')
    await select(wrapper, 'src')

    await select(wrapper, 'a.md')

    expect(tabTitles(wrapper)).toEqual(['a.md'])
    expect(readFile).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.zc-wb-tab').classes()).toContain('is-active')
  })

  it('渲染视图里的站内链接（open-file）按路径开页签并读盘', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')

    editor(wrapper).vm.$emit('open-file', 'src/inner.txt')
    await flushPromises()

    expect(tabTitles(wrapper)).toEqual(['a.md', 'inner.txt'])
    expect(editor(wrapper).props('path')).toBe('src/inner.txt')
    expect(readFile).toHaveBeenLastCalledWith(7, 'src/inner.txt')
  })

  it('关闭激活的中间页签 → 右邻激活；关闭最后一个 → 回空态', async () => {
    const wrapper = await mountWb()
    await expand(wrapper, 'src')
    await select(wrapper, 'a.md')
    await select(wrapper, 'src/inner.txt')
    await select(wrapper, 'src/lib') // 点目录行即展开
    await select(wrapper, 'src/lib/deep.txt')
    expect(tabTitles(wrapper)).toEqual(['a.md', 'inner.txt', 'deep.txt'])

    // 先激活中间那个再关
    await wrapper.findAll('.zc-wb-tab')[1].trigger('click')
    await closeTab(wrapper, 1)
    expect(tabTitles(wrapper)).toEqual(['a.md', 'deep.txt'])
    expect(editor(wrapper).props('path')).toBe('src/lib/deep.txt')

    await closeTab(wrapper, 1)
    expect(tabTitles(wrapper)).toEqual(['a.md'])
    expect(editor(wrapper).props('path')).toBe('a.md')

    await closeTab(wrapper, 0)
    expect(tabTitles(wrapper)).toEqual([])
    expect(editor(wrapper).props('path')).toBe('')
  })

  it('读取出错时内容区带 detail 与下载入口，页签仍可关闭', async () => {
    readFile.mockRejectedValue({ response: { data: { detail: '不支持读取的文件（二进制或超过200KB）' } } })
    const wrapper = await mountWb()

    await select(wrapper, 'a.md')

    expect(editor(wrapper).props('error')).toContain('不支持读取的文件')
    expect(tabTitles(wrapper)).toEqual(['a.md'])

    await closeTab(wrapper, 0)
    expect(tabTitles(wrapper)).toEqual([])
  })

  it('读取在途时关页签：迟到的响应不得写回内容', async () => {
    const gate = deferred()
    readFile.mockReturnValue(gate.promise)
    const wrapper = await mountWb()

    await select(wrapper, 'a.md')
    await closeTab(wrapper, 0)
    gate.resolve({ data: { content: '# 迟到的内容', mtime_ns: 1 } })
    await flushPromises()

    expect(editor(wrapper).props('path')).toBe('')
    expect(wrapper.text()).not.toContain('迟到的内容')
  })

  it('刷新只重置文件树，页签与内容保留', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')

    await wrapper.find('.zc-wb-refresh').trigger('click')
    await flushPromises()

    expect(listDir).toHaveBeenCalledTimes(2)
    expect(listDir).toHaveBeenLastCalledWith(7, '')
    expect(tabTitles(wrapper)).toEqual(['a.md'])
    expect(editor(wrapper).props('content')).toBe('# a.md')
    expect(readFile).toHaveBeenCalledTimes(1)
  })

  it('切换工作区：树、页签、编辑器全部重置，并按新 id 拉根层', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')

    await wrapper.setProps({ workspaceId: 8 })
    await flushPromises()

    expect(listDir).toHaveBeenLastCalledWith(8, '')
    expect(tabTitles(wrapper)).toEqual([])
    expect(editor(wrapper).props('path')).toBe('')
    expect(treeProps(wrapper).expandedKeys).toEqual([])
  })

  it('切换工作区时旧工作区迟到的根层响应不覆盖新树', async () => {
    const slow = deferred()
    listDir.mockImplementation((ws, p) => (ws === 7 ? slow.promise : fixture(ws, p)))
    const wrapper = await mountWb()

    await wrapper.setProps({ workspaceId: 8 })
    await flushPromises()
    expect(titles(wrapper)).toEqual(['src', 'a.md'])

    slow.resolve(ls('', [file('旧工作区.txt', '旧工作区.txt')]))
    await flushPromises()

    expect(titles(wrapper)).toEqual(['src', 'a.md'])
  })
})

describe('WorkspaceWorkbench 编辑与保存', () => {
  it('编辑后页签出现脏圆点；编辑器回报回到干净态后圆点消失', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')
    expect(dirty(wrapper)).toHaveLength(0)

    await markDirty(wrapper)
    expect(dirty(wrapper)).toHaveLength(1)

    // 撤销回保存时内容：Monaco 按 alternativeVersionId 回报 dirty=false
    editor(wrapper).vm.$emit('update:dirty', false)
    await flushPromises()
    expect(dirty(wrapper)).toHaveLength(0)
  })

  it('Ctrl+S 带 base_mtime_ns 保存：清脏、更新版本戳、状态栏短暂「已保存」且不弹 toast', async () => {
    saveFile.mockResolvedValue({ data: { ok: true, mtime_ns: 222 } })
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')
    await markDirty(wrapper)

    await pressSave(wrapper)

    expect(saveFile).toHaveBeenCalledWith({
      workspace_id: 7, path: 'a.md', content: '# 改过的内容', base_mtime_ns: 111,
    })
    expect(dirty(wrapper)).toHaveLength(0)
    expect(saveState(wrapper)).toContain('已保存')
    expect(message.success).not.toHaveBeenCalled()
    expect(editor(wrapper).props('savedContent')).toBe('# 改过的内容')
  })

  it('纳秒戳按字符串原样回传：超出 2^53 也不许转 Number', async () => {
    readFile.mockResolvedValueOnce({ data: { content: '# 原文', mtime_ns: '1793123456789012345' } })
    saveFile.mockResolvedValueOnce({ data: { ok: true, mtime_ns: '1793123456789012346' } })
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')
    await markDirty(wrapper)

    await pressSave(wrapper)

    expect(saveFile).toHaveBeenCalledWith({
      workspace_id: 7, path: 'a.md', content: '# 改过的内容', base_mtime_ns: '1793123456789012345',
    })

    saveFile.mockResolvedValueOnce({ data: { ok: true, mtime_ns: '1793123456789012347' } })
    await markDirty(wrapper, '# 第二版')
    await pressSave(wrapper)

    expect(saveFile).toHaveBeenLastCalledWith({
      workspace_id: 7, path: 'a.md', content: '# 第二版', base_mtime_ns: '1793123456789012346',
    })
  })

  it('保存期间继续输入：成功后只更新版本戳，脏态保持，下一次保存提交新内容', async () => {
    const gate = deferred()
    saveFile.mockReturnValueOnce(gate.promise)
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')
    await markDirty(wrapper, '# 第一版')

    await pressSave(wrapper) // 在途
    await markDirty(wrapper, '# 第二版') // 保存期间继续输入
    gate.resolve({ data: { ok: true, mtime_ns: 222 } })
    await flushPromises()

    expect(dirty(wrapper)).toHaveLength(1)
    expect(saveState(wrapper)).not.toContain('已保存')

    saveFile.mockResolvedValueOnce({ data: { ok: true, mtime_ns: 333 } })
    await pressSave(wrapper)

    expect(saveFile).toHaveBeenLastCalledWith({
      workspace_id: 7, path: 'a.md', content: '# 第二版', base_mtime_ns: 222,
    })
    expect(dirty(wrapper)).toHaveLength(0)
  })

  it('干净页签、无激活页签、读取错误页上按 Ctrl+S 都不发写请求', async () => {
    const wrapper = await mountWb()
    await pressSave(wrapper)
    expect(saveFile).not.toHaveBeenCalled()

    await select(wrapper, 'a.md')
    await pressSave(wrapper)
    expect(saveFile).not.toHaveBeenCalled()

    readFile.mockRejectedValue({ response: { data: { detail: '不支持读取的文件' } } })
    await closeTab(wrapper, 0)
    await select(wrapper, 'a.md')
    await pressSave(wrapper)
    expect(saveFile).not.toHaveBeenCalled()
  })

  it('保存失败：展示后端 detail，页签保持脏态，可再次保存', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')
    await markDirty(wrapper)
    saveFile.mockRejectedValueOnce({ response: { data: { detail: '没有写权限' } } })

    await pressSave(wrapper)

    expect(message.error).toHaveBeenCalledWith('没有写权限')
    expect(dirty(wrapper)).toHaveLength(1)
    expect(saveState(wrapper)).not.toContain('已保存')

    saveFile.mockResolvedValueOnce({ data: { ok: true, mtime_ns: 222 } })
    await pressSave(wrapper)
    expect(saveFile).toHaveBeenCalledTimes(2)
    expect(dirty(wrapper)).toHaveLength(0)
  })

  it('app.json 只读打开：状态栏标「只读」，Ctrl+S 不发请求', async () => {
    listDir.mockImplementation((ws, p) => (p === ''
      ? Promise.resolve(ls('', [file('app.json', 'app.json')]))
      : fixture(ws, p)))
    const wrapper = await mountWb()
    await select(wrapper, 'app.json')

    expect(editor(wrapper).props('readOnly')).toBe(true)
    expect(wrapper.find('.zc-wb-ro').text()).toBe('只读')

    await markDirty(wrapper) // 即使脏态被意外置上，也只读收口
    await pressSave(wrapper)
    expect(saveFile).not.toHaveBeenCalled()
  })

  it('关闭脏页签先确认：取消保留页签与脏态，确认后丢弃并关闭', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')
    await markDirty(wrapper)

    await closeTab(wrapper, 0)
    expect(tabTitles(wrapper)).toEqual(['a.md'])
    expect(wrapper.find('.zc-wb-close-modal').attributes('data-open')).toBe('true')

    await wrapper.find('.zc-wb-close-cancel').trigger('click')
    await flushPromises()
    expect(wrapper.find('.zc-wb-close-modal').attributes('data-open')).toBe('false')
    expect(tabTitles(wrapper)).toEqual(['a.md'])
    expect(dirty(wrapper)).toHaveLength(1)

    await closeTab(wrapper, 0)
    await wrapper.find('.zc-wb-close-discard').trigger('click')
    await flushPromises()
    expect(tabTitles(wrapper)).toEqual([])
    expect(editor(wrapper).props('path')).toBe('')
  })

  it('关闭干净页签不弹确认', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')

    await closeTab(wrapper, 0)

    expect(tabTitles(wrapper)).toEqual([])
    expect(wrapper.find('.zc-wb-close-modal').attributes('data-open')).toBe('false')
  })
})

describe('WorkspaceWorkbench 保存冲突（409）', () => {
  const conflict = (mtimeNs = 999) => ({
    response: {
      status: 409,
      data: { detail: '文件已被其他改动，请重新加载后再保存', mtime_ns: mtimeNs },
    },
  })
  const conflictOpen = (wrapper) => wrapper.find('.zc-wb-conflict').attributes('data-open')
  const clickConflict = async (wrapper, action) => {
    await wrapper.find(`.zc-wb-conflict-${action}`).trigger('click')
    await flushPromises()
  }

  async function dirtySave(wrapper, content = '# 本地改动') {
    await select(wrapper, 'a.md')
    await markDirty(wrapper, content)
    await pressSave(wrapper)
  }

  it('409 弹三按钮弹框并说明磁盘已被改动，不弹只带 detail 的 toast', async () => {
    saveFile.mockRejectedValueOnce(conflict())
    const wrapper = await mountWb()

    await dirtySave(wrapper)

    expect(conflictOpen(wrapper)).toBe('true')
    expect(wrapper.find('.zc-wb-conflict-text').text()).toContain('磁盘')
    expect(wrapper.find('.zc-wb-conflict-overwrite').exists()).toBe(true)
    expect(wrapper.find('.zc-wb-conflict-reload').exists()).toBe(true)
    expect(wrapper.find('.zc-wb-conflict-cancel').exists()).toBe(true)
    expect(message.error).not.toHaveBeenCalled()
    expect(dirty(wrapper)).toHaveLength(1)
  })

  it('覆盖：用 409 带回的 mtime_ns 重发同一份内容，成功后清脏并关框', async () => {
    saveFile.mockRejectedValueOnce(conflict(999))
    const wrapper = await mountWb()
    await dirtySave(wrapper)

    saveFile.mockResolvedValueOnce({ data: { ok: true, mtime_ns: 1000 } })
    await clickConflict(wrapper, 'overwrite')

    expect(saveFile).toHaveBeenLastCalledWith({
      workspace_id: 7, path: 'a.md', content: '# 本地改动', base_mtime_ns: 999,
    })
    expect(conflictOpen(wrapper)).toBe('false')
    expect(dirty(wrapper)).toHaveLength(0)
    expect(saveState(wrapper)).toContain('已保存')
  })

  it('覆盖时又被改（再次 409）：弹框保留并换成新 mtime，再覆盖不带旧壁纸强写', async () => {
    saveFile.mockRejectedValueOnce(conflict(999))
    const wrapper = await mountWb()
    await dirtySave(wrapper)

    saveFile.mockRejectedValueOnce(conflict(1234))
    await clickConflict(wrapper, 'overwrite')
    expect(conflictOpen(wrapper)).toBe('true')
    expect(message.error).not.toHaveBeenCalled()

    saveFile.mockResolvedValueOnce({ data: { ok: true, mtime_ns: 1300 } })
    await clickConflict(wrapper, 'overwrite')
    expect(saveFile).toHaveBeenLastCalledWith({
      workspace_id: 7, path: 'a.md', content: '# 本地改动', base_mtime_ns: 1234,
    })
    expect(conflictOpen(wrapper)).toBe('false')
    expect(dirty(wrapper)).toHaveLength(0)
  })

  it('重新加载：丢弃本地改动、重读磁盘、更新内容与 mtime 并清脏', async () => {
    readFile
      .mockResolvedValueOnce({ data: { content: '# 打开时的内容', mtime_ns: 111 } })
      .mockResolvedValueOnce({ data: { content: '# 磁盘上的新内容', mtime_ns: 999 } })
    saveFile.mockRejectedValueOnce(conflict())
    const wrapper = await mountWb()
    await dirtySave(wrapper)

    await clickConflict(wrapper, 'reload')

    expect(readFile).toHaveBeenCalledTimes(2)
    expect(conflictOpen(wrapper)).toBe('false')
    expect(editor(wrapper).props('content')).toBe('# 磁盘上的新内容')
    expect(editor(wrapper).props('savedContent')).toBe('# 磁盘上的新内容')
    expect(dirty(wrapper)).toHaveLength(0)

    // 重读之后按 Ctrl+S 不会再发写请求（干净页签）
    await pressSave(wrapper)
    expect(saveFile).toHaveBeenCalledTimes(1)
  })

  it('取消：关闭弹框，编辑内容与脏态原样保留', async () => {
    saveFile.mockRejectedValueOnce(conflict())
    const wrapper = await mountWb()
    await dirtySave(wrapper)

    await clickConflict(wrapper, 'cancel')

    expect(conflictOpen(wrapper)).toBe('false')
    expect(editor(wrapper).props('content')).toBe('# 本地改动')
    expect(dirty(wrapper)).toHaveLength(1)
  })

  it('弹框打开期间重复 Ctrl+S 不再并发发请求', async () => {
    saveFile.mockRejectedValueOnce(conflict())
    const wrapper = await mountWb()
    await dirtySave(wrapper)

    await pressSave(wrapper)
    await pressSave(wrapper)

    expect(saveFile).toHaveBeenCalledTimes(1)
    expect(conflictOpen(wrapper)).toBe('true')
  })
})

describe('WorkspaceWorkbench 页签恢复', () => {
  const storeKey = (id = 7) => tabsStorageKey(id)
  const readStore = (id = 7) => JSON.parse(localStorage.getItem(storeKey(id)))
  const stored = (tabs, active) => JSON.stringify({ tabs, active })

  it('挂载时恢复页签列表与激活项：只读取激活项，其余页签首次激活才读盘', async () => {
    localStorage.setItem(storeKey(), stored(['a.md', 'src/inner.txt'], 'src/inner.txt'))

    const wrapper = await mountWb()

    expect(tabTitles(wrapper)).toEqual(['a.md', 'inner.txt'])
    expect(wrapper.find('.zc-wb-tab.is-active').attributes('title')).toBe('src/inner.txt')
    expect(readFile).toHaveBeenCalledTimes(1)
    expect(readFile).toHaveBeenCalledWith(7, 'src/inner.txt')
    expect(editor(wrapper).props('content')).toBe('# src/inner.txt')
    expect(dirty(wrapper)).toHaveLength(0)

    await wrapper.findAll('.zc-wb-tab')[0].trigger('click')
    await flushPromises()
    expect(readFile).toHaveBeenCalledTimes(2)
    expect(readFile).toHaveBeenLastCalledWith(7, 'a.md')
  })

  it('打开/关闭/切换页签都会把列表与激活项写回 localStorage', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')
    expect(readStore()).toEqual({ tabs: ['a.md'], active: 'a.md' })

    await expand(wrapper, 'src')
    await select(wrapper, 'src/inner.txt')
    expect(readStore()).toEqual({ tabs: ['a.md', 'src/inner.txt'], active: 'src/inner.txt' })

    await wrapper.findAll('.zc-wb-tab')[0].trigger('click')
    await flushPromises()
    expect(readStore()).toEqual({ tabs: ['a.md', 'src/inner.txt'], active: 'a.md' })

    await closeTab(wrapper, 1)
    expect(readStore()).toEqual({ tabs: ['a.md'], active: 'a.md' })
  })

  it('恢复内容来自磁盘而不是旧草稿：存储里只有路径，脏态一律清零', async () => {
    localStorage.setItem(storeKey(), stored(['a.md'], 'a.md'))
    readFile.mockResolvedValue({ data: { content: '# 磁盘最新版本', mtime_ns: 777 } })

    const wrapper = await mountWb()

    expect(editor(wrapper).props('content')).toBe('# 磁盘最新版本')
    expect(editor(wrapper).props('savedContent')).toBe('# 磁盘最新版本')
    expect(dirty(wrapper)).toHaveLength(0)
  })

  it.each([
    ['坏 JSON', '{not json'],
    ['非对象', JSON.stringify(['a.md'])],
    ['tabs 非数组', JSON.stringify({ tabs: 'a.md', active: 'a.md' })],
    ['含非字符串项', JSON.stringify({ tabs: ['a.md', 1, null], active: 'a.md' })],
    ['空串', ''],
  ])('%s：按空列表处理并移除坏值，不抛错', async (_, raw) => {
    localStorage.setItem(storeKey(), raw)

    const wrapper = await mountWb()

    expect(tabTitles(wrapper)).toEqual([])
    expect(localStorage.getItem(storeKey())).toBeNull()
    expect(readFile).not.toHaveBeenCalled()
  })

  it('恢复列表里的重复路径去重，只留一个页签', async () => {
    localStorage.setItem(storeKey(), stored(['a.md', 'a.md'], 'a.md'))

    const wrapper = await mountWb()

    expect(tabTitles(wrapper)).toEqual(['a.md'])
    expect(readFile).toHaveBeenCalledTimes(1)
    expect(readStore()).toEqual({ tabs: ['a.md'], active: 'a.md' })
  })

  it('localStorage 写失败：编辑不受阻，只提示一次「页签无法记忆」', async () => {
    const spy = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    try {
      const wrapper = await mountWb()
      await select(wrapper, 'a.md')
      await expand(wrapper, 'src')
      await select(wrapper, 'src/inner.txt')

      expect(tabTitles(wrapper)).toEqual(['a.md', 'inner.txt'])
      expect(message.warning).toHaveBeenCalledTimes(1)
      expect(message.warning).toHaveBeenCalledWith('页签无法记忆')
    } finally {
      spy.mockRestore()
    }
  })

  it('切换工作区：按新 id 恢复它自己的页签，旧工作区的列表不串过去', async () => {
    localStorage.setItem(storeKey(7), stored(['a.md'], 'a.md'))
    localStorage.setItem(storeKey(8), stored(['src/inner.txt'], 'src/inner.txt'))
    const wrapper = await mountWb()
    expect(tabTitles(wrapper)).toEqual(['a.md'])

    await wrapper.setProps({ workspaceId: 8 })
    await flushPromises()

    expect(tabTitles(wrapper)).toEqual(['inner.txt'])
    expect(readFile).toHaveBeenLastCalledWith(8, 'src/inner.txt')
    expect(listDir).toHaveBeenCalledWith(8, '')
    expect(listDir).toHaveBeenCalledWith(8, 'src')
  })

  it('存在脏页签时 beforeunload 被拦截；全部干净时不拦截', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')

    const clean = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(clean)
    expect(clean.defaultPrevented).toBe(false)

    await markDirty(wrapper)
    const dirtyEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(dirtyEvent)
    expect(dirtyEvent.defaultPrevented).toBe(true)
  })
})

describe('WorkspaceWorkbench ?file 入口', () => {
  it('初始文件自动开页签，树逐级懒加载并展开到它所在目录', async () => {
    const wrapper = await mountWb({ initialFile: 'src/lib/deep.txt' })

    expect(tabTitles(wrapper)).toEqual(['deep.txt'])
    expect(editor(wrapper).props('path')).toBe('src/lib/deep.txt')
    expect(readFile).toHaveBeenCalledWith(7, 'src/lib/deep.txt')
    expect(listDir).toHaveBeenCalledWith(7, 'src')
    expect(listDir).toHaveBeenCalledWith(7, 'src/lib')
    expect(treeProps(wrapper).expandedKeys).toEqual(['src', 'src/lib'])
    expect(treeProps(wrapper).loadedKeys).toEqual(['src', 'src/lib'])
    expect(treeProps(wrapper).selectedKeys).toEqual(['src/lib/deep.txt'])
    expect(titles(wrapper)).toEqual(['src', 'lib', 'deep.txt', 'inner.txt', 'a.md'])
    expect(crumbs(wrapper)).toEqual(['演示工作区', 'src', 'lib'])
  })

  it('目标文件已在恢复列表里：只激活，不重复建页签、不重复读盘，树照样展开', async () => {
    localStorage.setItem(tabsStorageKey(7), JSON.stringify({ tabs: ['src/lib/deep.txt'], active: 'src/lib/deep.txt' }))

    const wrapper = await mountWb({ initialFile: 'src/lib/deep.txt' })

    expect(tabTitles(wrapper)).toEqual(['deep.txt'])
    expect(readFile).toHaveBeenCalledTimes(1)
    expect(treeProps(wrapper).expandedKeys).toEqual(['src', 'src/lib'])
  })

  it('文件不存在：页签展示读取错误，树只加载到能加载的层级且不弹错', async () => {
    readFile.mockRejectedValue({ response: { data: { detail: '文件不存在' } } })

    const wrapper = await mountWb({ initialFile: 'missing/deep.txt' })

    expect(tabTitles(wrapper)).toEqual(['deep.txt'])
    expect(editor(wrapper).props('error')).toBe('文件不存在')
    expect(message.error).not.toHaveBeenCalled()
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
  })

  it('展开途中某层目录拉失败：不弹错、不打断页签读取', async () => {
    listDir.mockImplementation((ws, p) => (p === 'src'
      ? Promise.reject({ response: { data: { detail: '目录不存在' } } })
      : fixture(ws, p)))

    const wrapper = await mountWb({ initialFile: 'src/lib/deep.txt' })

    expect(message.error).not.toHaveBeenCalled()
    expect(tabTitles(wrapper)).toEqual(['deep.txt'])
    expect(editor(wrapper).props('content')).toBe('# src/lib/deep.txt')
    expect(treeProps(wrapper).expandedKeys).toEqual([])
  })

  it('initialFile 为空串时不额外开页签', async () => {
    const wrapper = await mountWb({ initialFile: '' })

    expect(tabTitles(wrapper)).toEqual([])
    expect(readFile).not.toHaveBeenCalled()
  })
})

describe('WorkspaceWorkbench 视觉收官（图标 / 主题 / 状态栏）', () => {
  const root = (wrapper) => wrapper.find('.zc-wb')
  const statusText = (wrapper, cls) => wrapper.find(`.zc-wb-status .${cls}`).text()
  const iconPairs = (wrapper) => wrapper.findAll('.stub-icon')
    .map((i) => [i.attributes('data-path'), i.attributes('data-dir')])

  it('树与页签都带类型图标：目录 dir=true、文件 dir=false', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')

    const icons = iconPairs(wrapper)
    expect(icons).toContainEqual(['src', 'true'])
    expect(icons).toContainEqual(['a.md', 'false'])
    // 树里的 a.md + 页签上的 a.md 各一枚
    expect(icons.filter(([p]) => p === 'a.md')).toHaveLength(2)
  })

  it('默认深色：根节点 data-theme=dark，编辑器同步 dark', async () => {
    const wrapper = await mountWb()

    expect(root(wrapper).attributes('data-theme')).toBe('dark')
    expect(editor(wrapper).props('theme')).toBe('dark')
  })

  it('点状态栏主题按钮切浅色：立即生效、写入存储、再点回深色', async () => {
    const wrapper = await mountWb()

    await wrapper.find('.zc-wb-theme-toggle').trigger('click')
    expect(root(wrapper).attributes('data-theme')).toBe('light')
    expect(localStorage.getItem(THEME_KEY)).toBe('light')
    expect(editor(wrapper).props('theme')).toBe('light')

    await wrapper.find('.zc-wb-theme-toggle').trigger('click')
    expect(root(wrapper).attributes('data-theme')).toBe('dark')
    expect(localStorage.getItem(THEME_KEY)).toBe('dark')
    expect(editor(wrapper).props('theme')).toBe('dark')
  })

  it('挂载时应用已存的浅色主题；坏值一律回落深色', async () => {
    localStorage.setItem(THEME_KEY, 'light')
    const light = await mountWb()
    expect(root(light).attributes('data-theme')).toBe('light')

    localStorage.setItem(THEME_KEY, 'blue')
    const bad = await mountWb()
    expect(root(bad).attributes('data-theme')).toBe('dark')
  })

  it('状态栏显示语言、Ln/Col、编码与 EOL，无选区时不显示选中数', async () => {
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')

    expect(statusText(wrapper, 'zc-wb-lang')).toBe('Markdown')
    expect(statusText(wrapper, 'zc-wb-cursor')).toBe('Ln 1, Col 1')
    expect(statusText(wrapper, 'zc-wb-encoding')).toBe('UTF-8')
    expect(statusText(wrapper, 'zc-wb-eol')).toBe('LF')
    expect(wrapper.find('.zc-wb-selected').exists()).toBe(false)
    expect(wrapper.find('.zc-wb-save-state').exists()).toBe(true)
  })

  it('光标与选区上报后实时更新；CRLF 内容显示 CRLF', async () => {
    readFile.mockResolvedValue({ data: { content: 'a\r\nb\r\nc', mtime_ns: 5 } })
    const wrapper = await mountWb()
    await select(wrapper, 'a.md')

    editor(wrapper).vm.$emit('cursor', { line: 3, column: 5, selected: 12 })
    await flushPromises()

    expect(statusText(wrapper, 'zc-wb-cursor')).toBe('Ln 3, Col 5')
    expect(statusText(wrapper, 'zc-wb-selected')).toBe('已选 12')
    expect(statusText(wrapper, 'zc-wb-eol')).toBe('CRLF')
  })

  it('切页签回深层文件：树选中同步并逐级展开', async () => {
    const wrapper = await mountWb()
    await expand(wrapper, 'src')
    await expand(wrapper, 'src/lib')
    await select(wrapper, 'src/lib/deep.txt')
    await select(wrapper, 'a.md')
    await expand(wrapper, 'src/lib') // 先收子级，再收顶层，树里只剩根层
    await expand(wrapper, 'src')
    expect(treeProps(wrapper).expandedKeys).toEqual([])

    await wrapper.findAll('.zc-wb-tab')
      .find((t) => t.attributes('title') === 'src/lib/deep.txt')
      .trigger('click')
    await flushPromises()

    expect(treeProps(wrapper).selectedKeys).toEqual(['src/lib/deep.txt'])
    expect(treeProps(wrapper).expandedKeys).toEqual(['src', 'src/lib'])
    expect(titles(wrapper)).toContain('deep.txt')
  })

  it('无激活文件时状态栏不显示语言与光标，只留保存位与主题入口', async () => {
    const wrapper = await mountWb()

    expect(wrapper.find('.zc-wb-lang').exists()).toBe(false)
    expect(wrapper.find('.zc-wb-cursor').exists()).toBe(false)
    expect(wrapper.find('.zc-wb-save-state').exists()).toBe(true)
    expect(wrapper.find('.zc-wb-theme-toggle').exists()).toBe(true)
  })
})
