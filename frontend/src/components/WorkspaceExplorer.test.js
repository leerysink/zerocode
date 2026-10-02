// @vitest-environment happy-dom
// 内置文件管理器（左树右预览，整页不是弹框）：目录树按 GET /api/fs/ls 懒加载，展开一层才拉一层；右侧只预览文件。
// 挂载即拉根层：要不要拉、拿哪个 id 拉，是 WorkspaceFilesView 按地址判完之后的事，组件不再接收 open。
// a-tree 桩严格照 ant-design-vue 4.x 的懒加载契约模拟：展开时若该 key 不在 loadedKeys 才调
// props.loadData(node)，resolve 之后才 emit update:loadedKeys —— 所以组件必须自己回写 loadedKeys，
// 桩才会把它记成「已加载」。expandAction='click' 时点行 = 先展开再 select（vc-tree/TreeNode.js:218-228
// → Tree.js:523-534），且展开与 loadData 都不判断叶子（Tree.js:511-522 / :792），桩一律照抄。
// 照这个契约写的用例，换真组件时行为一致。
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
import { message } from 'ant-design-vue'
import WorkspaceExplorer from './WorkspaceExplorer.vue'

const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})

// 只渲染「当前展开态下可见」的那条路径，跟 ant 的 flattenedTreeKeys 一个口径
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
    // ant：先算新 expandedKeys 并 emit，再「目标为展开且配了 loadData」才去拉这一层
    toggle(node) {
      const keys = (this.expandedKeys || []).slice()
      const at = keys.indexOf(node.key)
      if (at >= 0) keys.splice(at, 1)
      else keys.push(node.key)
      this.$emit('update:expandedKeys', keys)
      this.$emit('expand', keys, { node, expanded: at < 0 })
      if (at < 0 && this.loadData && !(this.loadedKeys || []).includes(node.key)) {
        // ant 的 onNodeLoad 兜住 loadData 的 rejection（失败只算“没加载成”，不记 loadedKeys）；
        // 但 onNodeExpand 的展开恢复链里还会再抛一个没人接的 rejection（Tree.js:803-807），
        // 那是 ant 自己的控制台噪音、对组件行为没有影响，桩不复刻它。
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
      // ant 的 onSelectorClick：先 onNodeClick（expandAction='click' 时展开，不看是不是叶子），再 onSelect。
      // 顺序照抄，组件才会被真实顺序考到；没配 expandAction 时点行就只是选中。
      if (this.expandAction === 'click') this.toggle(node)
      // ant 的 selectable 不区分目录与叶子：目录也会发 select，组件要自己判断“目录不该进预览”；
      // 已选中的再点一次 = 取消选中（Tree.js:547-565 的 targetSelected 取反），keys 会是空数组。
      const selected = (this.selectedKeys || []).includes(node.key)
      const keys = selected ? [] : [node.key]
      this.$emit('update:selectedKeys', keys)
      this.$emit('select', keys, { node, selected: !selected })
    },
  },
  render() {
    const rows = this.flatten(this.treeData, []).map((n) => h('div', { class: 'stub-tree-node', key: n.key, 'data-key': n.key }, [
      h('span', { class: 'stub-tree-switch', onClick: () => this.toggle(n) }),
      h('span', { class: 'stub-tree-title', onClick: () => this.pick(n) }, n.title),
    ]))
    return h('div', { class: 'stub-tree' }, rows)
  },
}

const STUBS = {
  'a-button': slotDiv('a-button'),
  'a-tree': TreeStub,
  'a-spin': slotDiv('a-spin'),
  'a-empty': slotDiv('a-empty'),
  'a-tooltip': slotDiv('a-tooltip'),
}

const dir = (name, path) => ({ name, path, is_dir: true, size: 0, mtime: 1757900000 })
const file = (name, path, size = 12) => ({ name, path, is_dir: false, size, mtime: 1757900000 })
const ls = (path, entries) => ({ data: { path, parent: path || '', entries } })
// 根层给「目录 src + 文件 a.md」，src 里给「目录 lib + 文件 inner.txt」，lib 里给 deep.txt
const LAYERS = {
  '': [dir('src', 'src'), file('a.md', 'a.md')],
  src: [dir('lib', 'src/lib'), file('inner.txt', 'src/inner.txt')],
  'src/lib': [file('deep.txt', 'src/lib/deep.txt')],
}
const fixture = (ws, path) => Promise.resolve(ls(path, LAYERS[path] || []))

async function mountExplorer(props = {}) {
  const wrapper = mount(WorkspaceExplorer, {
    props: { workspaceId: 7, ...props },
    global: { stubs: STUBS },
  })
  await flushPromises()
  return wrapper
}

const titles = (wrapper) => wrapper.findAll('.stub-tree-title').map((t) => t.text())
// 受控 props 就是组件对 a-tree 的对外契约，直接读出来断言比查 DOM 稳
const treeProps = (wrapper) => wrapper.findComponent({ name: 'a-tree' }).props()
const crumbs = (wrapper) => wrapper.findAll('.zc-wex-crumb').map((c) => c.text())
// 手工可控的 promise：造“慢响应/过期响应”这类竞态，落地时机必须由用例自己决定
function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}
// 按名称展开某个目录节点（走桩的 switch，等价于点 ant 的展开箭头）
async function expand(wrapper, key) {
  await wrapper.find(`.stub-tree-node[data-key="${key}"] .stub-tree-switch`).trigger('click')
  await flushPromises()
}
async function select(wrapper, key) {
  await wrapper.find(`.stub-tree-node[data-key="${key}"] .stub-tree-title`).trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  listDir.mockImplementation(fixture)
  readFile.mockResolvedValue({ data: { content: '# 标题' } })
})

describe('WorkspaceExplorer 懒加载树', () => {
  it('进页面就拉根层一次，条目渲染成树节点', async () => {
    const wrapper = await mountExplorer()

    expect(listDir).toHaveBeenCalledTimes(1)
    expect(listDir).toHaveBeenCalledWith(7, '')
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
  })

  it('字符串 id（直链地址带来的）照常拉根层，不转成 Number', async () => {
    const wrapper = await mountExplorer({ workspaceId: '2026092201000900123' })

    expect(listDir).toHaveBeenCalledWith('2026092201000900123', '')
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
  })

  it('展开目录才请求那一层，并把返回条目挂成子节点', async () => {
    const wrapper = await mountExplorer()

    await expand(wrapper, 'src')

    expect(listDir).toHaveBeenCalledTimes(2)
    expect(listDir).toHaveBeenLastCalledWith(7, 'src')
    expect(titles(wrapper)).toEqual(['src', 'lib', 'inner.txt', 'a.md'])
  })

  it('收起再展开同一目录不再请求（loadedKeys 命中缓存）', async () => {
    const wrapper = await mountExplorer()

    await expand(wrapper, 'src')
    await expand(wrapper, 'src') // 收起
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
    await expand(wrapper, 'src') // 再展开

    expect(listDir).toHaveBeenCalledTimes(2) // 初次 + 第一次展开
  })

  it('深层目录逐级展开各拉一次，路径带全（不是只带目录名）', async () => {
    const wrapper = await mountExplorer()

    await expand(wrapper, 'src')
    await expand(wrapper, 'src/lib')

    expect(listDir).toHaveBeenNthCalledWith(3, 7, 'src/lib')
    expect(titles(wrapper)).toEqual(['src', 'lib', 'deep.txt', 'inner.txt', 'a.md'])
  })

})

describe('WorkspaceExplorer 点行即展开（类桌面）', () => {
  it('点目录行就展开这一层，不必去点小箭头，且不请求文件内容', async () => {
    const wrapper = await mountExplorer()

    await select(wrapper, 'src')

    expect(treeProps(wrapper).expandAction).toBe('click')
    expect(treeProps(wrapper).expandedKeys).toEqual(['src'])
    expect(listDir).toHaveBeenCalledTimes(2)
    expect(listDir).toHaveBeenLastCalledWith(7, 'src')
    expect(titles(wrapper)).toEqual(['src', 'lib', 'inner.txt', 'a.md'])
    expect(readFile).not.toHaveBeenCalled()
  })

  it('点文件行不把文件塞进展开态，也不拿文件路径去请求目录', async () => {
    const wrapper = await mountExplorer()
    await expand(wrapper, 'src')

    await select(wrapper, 'src/inner.txt')

    // ant 对叶子一样会 toggle 并调 loadData（Tree.js:511-522 不判断叶子），得组件自己挡
    expect(treeProps(wrapper).expandedKeys).toEqual(['src'])
    expect(treeProps(wrapper).loadedKeys).toEqual(['src'])
    expect(listDir).toHaveBeenCalledTimes(2) // 根 + src，没有为文件多发一次
    expect(listDir).not.toHaveBeenCalledWith(7, 'src/inner.txt')
    expect(readFile).toHaveBeenCalledWith(7, 'src/inner.txt')
  })

  it('已选中的文件再点一次（ant 发的是取消选中）不把预览清成空白', async () => {
    const wrapper = await mountExplorer()
    await select(wrapper, 'a.md')
    const calls = readFile.mock.calls.length

    await select(wrapper, 'a.md')

    expect(readFile).toHaveBeenCalledTimes(calls)
    expect(wrapper.find('.zc-wex-preview').text()).toContain('# 标题')
    expect(wrapper.find('.zc-wex-error').exists()).toBe(false)
    expect(treeProps(wrapper).selectedKeys).toEqual(['a.md'])
  })
})

describe('WorkspaceExplorer 竞态与过期响应', () => {
  it('展开在途时点刷新重开：迟到的响应不得挂进新树，也不得记成已加载', async () => {
    const slow = deferred()
    listDir.mockImplementation((ws, p) => (p === 'src' ? slow.promise : fixture(ws, p)))
    const wrapper = await mountExplorer()

    await wrapper.find('.stub-tree-node[data-key="src"] .stub-tree-switch').trigger('click')
    await wrapper.find('.zc-wex-refresh').trigger('click')
    await flushPromises()
    slow.resolve(ls('src', LAYERS.src))
    await flushPromises()

    expect(treeProps(wrapper).treeData[0].children).toBeUndefined()
    expect(treeProps(wrapper).loadedKeys).toEqual([])
    expect(treeProps(wrapper).expandedKeys).toEqual([])
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
  })

  it('切换工作区时，上一份工作区迟到的根层响应不覆盖新树', async () => {
    const slow = deferred()
    listDir.mockImplementation((ws, p) => (ws === 7 ? slow.promise : fixture(ws, p)))
    const wrapper = await mountExplorer()

    await wrapper.setProps({ workspaceId: 8 })
    await flushPromises()
    expect(titles(wrapper)).toEqual(['src', 'a.md'])

    slow.resolve(ls('', [file('旧工作区.txt', '旧工作区.txt')]))
    await flushPromises()

    expect(titles(wrapper)).toEqual(['src', 'a.md'])
    expect(listDir).toHaveBeenLastCalledWith(8, '')
  })

  it('连点两个文件：慢的后到不覆盖快的先到，也不留错误态', async () => {
    const wrapper = await mountExplorer()
    await expand(wrapper, 'src')
    const slow = deferred()
    const fast = deferred()
    readFile.mockImplementation((ws, p) => (p === 'a.md' ? slow.promise : fast.promise))

    await select(wrapper, 'a.md')
    await select(wrapper, 'src/inner.txt')
    fast.resolve({ data: { content: '# 后点的先回来' } })
    await flushPromises()
    slow.resolve({ data: { content: '# 先点的后回来' } })
    await flushPromises()

    expect(wrapper.find('.zc-wex-meta-path').text()).toBe('src/inner.txt')
    expect(wrapper.find('.zc-wex-preview').text()).toBe('# 后点的先回来')
    expect(wrapper.find('.zc-wex-error').exists()).toBe(false)
  })

  it('预览在途时切工作区：迟到的响应既不进右栏，也不把加载态留在原地', async () => {
    const gate = deferred()
    readFile.mockReturnValue(gate.promise)
    const wrapper = await mountExplorer()

    await select(wrapper, 'a.md')
    expect(wrapper.vm.previewLoading).toBe(true)

    await wrapper.setProps({ workspaceId: 8 })
    gate.resolve({ data: { content: '# 迟到的旧工作区内容' } })
    await flushPromises()

    // reset 必须把预览的三个态一起清干净：迟到的响应走过期分支，不会再替它收尾
    expect(wrapper.vm.previewLoading).toBe(false)
    expect(wrapper.vm.previewPath).toBe('')
    expect(wrapper.text()).not.toContain('迟到的旧工作区内容')
  })

  it('同时展开两层：两层的已加载标记都得留下（覆盖式赋值会丢掉先落地的那层）', async () => {
    const layers = {
      '': [dir('a', 'a'), dir('b', 'b')],
      a: [file('a.txt', 'a/a.txt')],
      b: [file('b.txt', 'b/b.txt')],
    }
    const gates = { a: deferred(), b: deferred() }
    listDir.mockImplementation((ws, p) => (
      gates[p] ? gates[p].promise : Promise.resolve(ls(p, layers[p] || []))
    ))
    const wrapper = await mountExplorer()

    // 两层都点开，两个请求同时在途（不逐个等它们落地）
    await wrapper.find('.stub-tree-node[data-key="a"] .stub-tree-switch').trigger('click')
    await wrapper.find('.stub-tree-node[data-key="b"] .stub-tree-switch').trigger('click')
    gates.b.resolve(ls('b', layers.b))
    await flushPromises()
    gates.a.resolve(ls('a', layers.a))
    await flushPromises()

    expect(treeProps(wrapper).expandedKeys).toEqual(['a', 'b'])
    expect([...treeProps(wrapper).loadedKeys].sort()).toEqual(['a', 'b'])
    expect(titles(wrapper)).toEqual(['a', 'a.txt', 'b', 'b.txt'])

    // 两层都真加载过了：收起再展开不重拉（丢 key 的直接后果就是白跑一次请求）
    const calls = listDir.mock.calls.length
    await expand(wrapper, 'a')
    await expand(wrapper, 'a')
    expect(listDir).toHaveBeenCalledTimes(calls)
  })

  it('刷新在途时再点一次刷新：前一次迟到的根层响应不覆盖新树', async () => {
    const slow = deferred()
    let rootCalls = 0
    listDir.mockImplementation((ws, p) => {
      if (p !== '') return fixture(ws, p)
      rootCalls += 1
      return rootCalls === 1 ? slow.promise : fixture(ws, p)
    })
    const wrapper = await mountExplorer() // 第一次根层始终在途

    expect(titles(wrapper)).toEqual([])

    await wrapper.find('.zc-wex-refresh').trigger('click') // 第二次刷新先回来
    await flushPromises()
    expect(titles(wrapper)).toEqual(['src', 'a.md'])

    slow.resolve(ls('', [file('迟到的旧数据.txt', '迟到的旧数据.txt')]))
    await flushPromises()

    expect(titles(wrapper)).toEqual(['src', 'a.md'])
    // 过期分支不许替新一次收尾：加载态只能由当前世代自己落下
    expect(wrapper.vm.rootLoading).toBe(false)
  })

  it('展开某层失败：面包屑退回还看得见的那一层，不停在失败层', async () => {
    listDir.mockImplementation((ws, p) => (p === 'src'
      ? Promise.reject({ response: { data: { detail: '目录不存在或已被删除' } } })
      : fixture(ws, p)))
    const wrapper = await mountExplorer()

    await expand(wrapper, 'src')

    expect(crumbs(wrapper)).toEqual(['工作区'])
  })

  it('深层展开失败：面包屑退回上一层可见目录，展开态也收回去', async () => {
    listDir.mockImplementation((ws, p) => (p === 'src/lib'
      ? Promise.reject({ response: { data: { detail: '目录不存在或已被删除' } } })
      : fixture(ws, p)))
    const wrapper = await mountExplorer()
    await expand(wrapper, 'src')

    await expand(wrapper, 'src/lib')

    expect(treeProps(wrapper).expandedKeys).toEqual(['src'])
    expect(crumbs(wrapper)).toEqual(['工作区', 'src'])
  })
})

describe('WorkspaceExplorer 只读预览', () => {
  it('点文件才拉内容，预览区展示文本', async () => {
    const wrapper = await mountExplorer()

    await select(wrapper, 'a.md')

    expect(readFile).toHaveBeenCalledTimes(1)
    expect(readFile).toHaveBeenCalledWith(7, 'a.md')
    expect(wrapper.find('.zc-wex-preview').text()).toContain('# 标题')
  })

  it('预览区带上大小/修改时间与下载链接（跟桌面属性栏一个口径）', async () => {
    const wrapper = await mountExplorer()

    await select(wrapper, 'a.md')

    expect(wrapper.find('.zc-wex-meta').text()).toContain('12 B')
    expect(wrapper.find('.zc-wex-meta').text()).toMatch(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/)
    expect(wrapper.find('.zc-wex-download').attributes('href'))
      .toBe('/api/fs/download?workspace_id=7&path=a.md')
  })

  it('后端拒读（二进制/超 200KB）时把原因留在预览区，下载链接照旧给出', async () => {
    listDir.mockResolvedValueOnce(ls('', [file('big.bin', 'big.bin', 999999)]))
    const wrapper = await mountExplorer()
    readFile.mockRejectedValue({ response: { data: { detail: '不支持读取的文件（二进制或超过200KB）' } } })

    await select(wrapper, 'big.bin')

    expect(wrapper.find('.zc-wex-error').text()).toContain('不支持读取的文件')
    expect(wrapper.find('.zc-wex-preview').exists()).toBe(false)
    expect(wrapper.find('.zc-wex-download').attributes('href')).toContain('path=big.bin')
  })

  it('拒读时不留下 undefined 文案', async () => {
    const wrapper = await mountExplorer()
    readFile.mockRejectedValue({})

    await select(wrapper, 'a.md')

    expect(wrapper.find('.zc-wex-error').text()).toBe('读取失败')
  })

  it('选中深层文件时面包屑落到它所在的那一层', async () => {
    const wrapper = await mountExplorer()
    await expand(wrapper, 'src')
    await expand(wrapper, 'src/lib')

    await select(wrapper, 'src/lib/deep.txt')

    expect(wrapper.findAll('.zc-wex-crumb').map((c) => c.text())).toEqual(['工作区', 'src', 'lib'])
    expect(readFile).toHaveBeenCalledWith(7, 'src/lib/deep.txt')
  })
})

describe('WorkspaceExplorer 展开态与异常边界', () => {
  it('刷新把展开态与 loadedKeys 一起重置，不会拿旧缓存给新数据', async () => {
    const wrapper = await mountExplorer()
    await expand(wrapper, 'src')
    expect(treeProps(wrapper).loadedKeys).toEqual(['src'])

    await wrapper.find('.zc-wex-refresh').trigger('click')
    await flushPromises()

    expect(treeProps(wrapper).expandedKeys).toEqual([])
    expect(treeProps(wrapper).loadedKeys).toEqual([])
    expect(listDir).toHaveBeenCalledTimes(3) // 初次 + 展开 src + 刷新重拉根层
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
  })

  it('切换工作区：按新 id 重拉根层，旧展开态不带过去', async () => {
    const wrapper = await mountExplorer()
    await expand(wrapper, 'src')

    await wrapper.setProps({ workspaceId: 8 })
    await flushPromises()

    expect(listDir).toHaveBeenLastCalledWith(8, '')
    expect(treeProps(wrapper).expandedKeys).toEqual([])
    expect(treeProps(wrapper).loadedKeys).toEqual([])
  })

  it('刷新等于回到根层重来，不拿旧展开态去“刷新”深层', async () => {
    const wrapper = await mountExplorer()
    await expand(wrapper, 'src')

    await wrapper.find('.zc-wex-refresh').trigger('click')
    await flushPromises()

    expect(listDir).toHaveBeenCalledTimes(3) // 初次 + src + 刷新根层
    expect(listDir).toHaveBeenLastCalledWith(7, '')
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
  })

  it('展开某层失败：提示原因、这一层不收成展开态也不记进 loadedKeys（再展开可重试）', async () => {
    listDir.mockImplementation((ws, p) => (p === 'src'
      ? Promise.reject({ response: { data: { detail: '目录不存在或已被删除' } } })
      : fixture(ws, p)))
    const wrapper = await mountExplorer()

    await expand(wrapper, 'src')

    expect(message.error).toHaveBeenCalledWith('目录不存在或已被删除')
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
    expect(treeProps(wrapper).loadedKeys).toEqual([])
    expect(treeProps(wrapper).expandedKeys).toEqual([])

    listDir.mockImplementation(fixture) // 恢复后重试就能拿到
    await expand(wrapper, 'src')
    expect(titles(wrapper)).toContain('inner.txt')
  })

  it('失败时没有 detail（网络层错误）也不弹 undefined', async () => {
    listDir.mockImplementation((ws, p) => (p === 'src' ? Promise.reject({}) : fixture(ws, p)))
    const wrapper = await mountExplorer()

    await expand(wrapper, 'src')

    expect(message.error).toHaveBeenCalledWith('目录加载失败')
  })

  it('根层就没有条目：空目录占位，不渲染空树也不报错', async () => {
    listDir.mockResolvedValueOnce(ls('', []))

    const wrapper = await mountExplorer()

    expect(wrapper.find('.stub-a-empty').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'a-tree' }).exists()).toBe(false)
    expect(message.error).not.toHaveBeenCalled()
  })

  it('根层拉失败：不留下半截树，提示后仍是空态', async () => {
    listDir.mockRejectedValue({ response: { data: { detail: '工作区不存在' } } })

    const wrapper = await mountExplorer()

    expect(message.error).toHaveBeenCalledWith('工作区不存在')
    expect(wrapper.find('.stub-a-empty').exists()).toBe(true)
    expect(titles(wrapper)).toEqual([])
  })

  it('面包屑只折回已加载的那一层，不发新请求', async () => {
    const wrapper = await mountExplorer()
    await expand(wrapper, 'src')
    await expand(wrapper, 'src/lib')
    const before = listDir.mock.calls.length

    await wrapper.findAll('.zc-wex-crumb')[1].trigger('click') // src
    await flushPromises()
    expect(listDir).toHaveBeenCalledTimes(before)
    expect(titles(wrapper)).toEqual(['src', 'lib', 'inner.txt', 'a.md'])

    await wrapper.find('.zc-wex-crumb-root').trigger('click') // 回到工作区根
    await flushPromises()
    expect(listDir).toHaveBeenCalledTimes(before)
    expect(titles(wrapper)).toEqual(['src', 'a.md'])
  })

  it('中文与含空格路径的目录名原样展示，请求带全路径', async () => {
    listDir.mockImplementation((ws, p) => Promise.resolve(
      p === '' ? ls('', [dir('资源 目录', '资源 目录')]) : ls(p, [file('说明.md', `${p}/说明.md`)]),
    ))

    const wrapper = await mountExplorer()
    await expand(wrapper, '资源 目录')

    expect(listDir).toHaveBeenLastCalledWith(7, '资源 目录')
    expect(titles(wrapper)).toEqual(['资源 目录', '说明.md'])
  })
})
