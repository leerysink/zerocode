// @vitest-environment happy-dom
// Task 10：AppsView 发布接线（最小挂载断言）——M2 占位文案撤掉、发布/历史入口上卡、
// 栈闸门（可发布栈放行、static 置灰带说明；白名单与后端同源，漂移由
// tests/test_deploy_basics.py 钉住）。弹层内部行为在 PublishModal.test 覆盖。
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: {
    listApps: vi.fn(), createApp: vi.fn(), validateAppSlug: vi.fn(),
    devStart: vi.fn(), devStop: vi.fn(), devRestart: vi.fn(), devReset: vi.fn(), devStatus: vi.fn(),
    prodStart: vi.fn(), prodStop: vi.fn(), prodRestart: vi.fn(), prodStatus: vi.fn(),
    appUrls: vi.fn(), publishApp: vi.fn(), rollbackApp: vi.fn(),
    appRecords: vi.fn(), deleteRecord: vi.fn(), systemNics: vi.fn(), putConfig: vi.fn(),
    releaseToStore: vi.fn(), renameApp: vi.fn(), deleteApp: vi.fn(), openFolder: vi.fn(),
    appMetricsCurrent: vi.fn(),
    wsUrl: vi.fn(() => 'ws://test'),
  },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn() } },
  // 系统参数桩：form 是可变对象，单条用例改 openFolderMode 就能验证三种裁决分支
  cfg: { ensureLoaded: vi.fn(async () => {}), form: { openFolderMode: 'auto' } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)
// AppsView 顶层调 useConfigStore()：无 pinia 会挂载即抛，这里给一个只读桩
vi.mock('../stores/config', () => ({ useConfigStore: () => mocks.cfg }))

import AppsView from './AppsView.vue'

const APPS = [
  { id: 1, name: '甲', slug: 'aaa', stack: 'vue3-fastapi', scale: 'small', dev_port: 8301,
    prod_port: 8401, status_dev: 'stopped', status_prod: 'running', current_version: 'b20260920100000',
    preview_url: 'http://10.0.1.25:8301',
    workspace_id: 11 },
  { id: 2, name: '乙', slug: 'bbb', stack: 'static', scale: 'small', dev_port: 8302,
    prod_port: 8402, status_dev: 'stopped', status_prod: 'stopped', current_version: null,
    preview_url: 'http://10.0.1.25:8302',
    workspace_id: 12 },
  { id: 3, name: '丙', slug: 'ccc', stack: 'vue3-springboot', scale: 'large', dev_port: 8303,
    prod_port: 8403, status_dev: 'running', status_prod: 'stopped', current_version: null,
    preview_url: 'http://10.0.1.25:8303',
    workspace_id: 13 },
]

const slot = (name) => ({
  name, props: ['disabled', 'title', 'type', 'size', 'href', 'target'],
  render() {
    const attrs = { class: `stub-${name}` }
    if (this.disabled) attrs.disabled = ''
    if (this.title) attrs.title = this.title
    if (this.href) attrs.href = this.href
    return h(name === 'a-button' ? 'button' : 'div', attrs, this.$slots.default?.())
  },
})
const STUBS = {
  'a-card': { name: 'a-card', render() {
    return h('div', { class: 'stub-card' }, [
      this.$slots.title?.(), this.$slots.extra?.(), this.$slots.default?.()]) } },
  'a-space': slot('a-space'),
  'a-button': slot('a-button'),
  'a-tag': slot('a-tag'),
  'a-modal': { name: 'a-modal', props: ['open', 'width', 'title', 'centered', 'footer'],
    render() { return h('div', { class: 'stub-a-modal' }, [this.$slots.title?.(), this.$slots.default?.()]) } },
  'a-drawer': { name: 'a-drawer', props: ['open'], render() {
    return this.open ? h('div', { class: 'stub-drawer' }, this.$slots.default?.()) : null } },
  'a-spin': slot('a-spin'),
  'a-empty': slot('a-empty'),
  'a-steps': slot('a-steps'),
  'a-form': slot('a-form'),
  'a-form-item': slot('a-form-item'),
  'a-input': slot('a-input'),
  'a-select': slot('a-select'),
  'a-tooltip': slot('a-tooltip'),
  'a-dropdown': { name: 'a-dropdown', render() {
    return h('div', { class: 'stub-dropdown' }, [this.$slots.default?.(), this.$slots.overlay?.()]) } },
  BackButton: { name: 'BackButton', render() { return h('button', { class: 'stub-back' }, '返回') } },
  PublishModal: { name: 'PublishModal', render() { return h('div', { class: 'stub-pub-modal' }) } },
  PublishHistory: { name: 'PublishHistory', render() { return h('div', { class: 'stub-pub-history' }) } },
  MetricsModal: { name: 'MetricsModal', props: ['app'],
    render() { return h('div', { class: 'stub-metrics-modal' }, String(this.app?.id ?? '')) } },
}

async function mountView() {
  mocks.api.listApps.mockResolvedValue({ data: APPS })
  const wrapper = mount(AppsView, { global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

// execCommand 是浏览器 API，happy-dom 没实现，只能桩。桩里读真实 DOM：断言的是「复制那一刻页面上
// 那个临时节点承载的文本」，也就是降级路径真的把地址搬进了可被选中的节点，而不是桩自己记的参数。
function stubExecCommand(copiedText, impl = () => true) {
  const spy = vi.fn(() => {
    expect(document.querySelector('textarea')?.value).toBe(copiedText)
    return impl()
  })
  Object.defineProperty(document, 'execCommand', { value: spy, configurable: true })
  return spy
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.cfg.form.openFolderMode = 'auto'
  vi.spyOn(window, 'setInterval').mockReturnValue(0)
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete document.execCommand
  document.body.innerHTML = ''
})

describe('AppsView 发布接线', () => {
  it('M2 占位文案已撤，正式版行展示 prod 状态与版本', async () => {
    const wrapper = await mountView()
    const text = wrapper.text()
    expect(text).not.toContain('正式发布：M2 即将上线')
    const cards = wrapper.findAll('.stub-card')
    expect(cards[0].text()).toContain('运行中')
    expect(cards[0].text()).toContain('b20260920100000')
  })

  it('卡片操作行：发布 / 发布历史 / 正式环境启停入口', async () => {
    const wrapper = await mountView()
    const text = wrapper.findAll('.stub-card')[0].text()
    expect(text).toContain('发布')
    expect(text).toContain('发布历史')
    expect(text).toContain('停止正式')   // running → 提供停止
    expect(wrapper.findAll('.stub-card')[1].text()).toContain('启动正式')
  })

  it('单行内的环境分段：测试环境组放预览启停/重启，竖线后生产环境组放发布/正式启停', async () => {
    const wrapper = await mountView()
    const card = wrapper.findAll('.stub-card')[0]
    const acts = card.find('.zc-acts')
    expect(acts.exists()).toBe(true)
    const dev = acts.find('.zc-env-dev')
    const prod = acts.find('.zc-env-prod')
    expect(dev.exists()).toBe(true)
    expect(prod.exists()).toBe(true)
    // 两段在同一行容器里，中间有竖线分隔
    expect(acts.findAll('.zc-env-group').length).toBe(2)
    expect(acts.find('.zc-env-split').exists()).toBe(true)
    expect(dev.text()).toContain('测试环境')
    expect(dev.text()).toContain('启动预览')   // status_dev=stopped
    expect(dev.text()).toContain('重启')
    expect(dev.text()).not.toContain('发布')
    expect(prod.text()).toContain('生产环境')
    expect(prod.text()).toContain('发布')
    expect(prod.text()).toContain('停止正式')   // status_prod=running
    expect(prod.text()).not.toContain('预览')
  })

  it('栈闸门：可发布栈（fastapi/springboot）放行，不可发布栈（static）置灰带说明', async () => {
    const wrapper = await mountView()
    const pubBtn = (i) => wrapper.findAll('.stub-card')[i].findAll('.stub-a-button')
      .find((b) => b.text() === '发布')
    // 大型栈（vue3-springboot）后端早已可发布，闸门曾因只认 fastapi 把这张按钮钉死一个迭代
    expect(pubBtn(2).attributes('disabled')).toBeUndefined()
    expect(pubBtn(0).attributes('disabled')).toBeUndefined()
    const staticBtn = pubBtn(1)
    expect(staticBtn.attributes('disabled')).toBeDefined()
    expect(staticBtn.attributes('title')).toContain('暂不支持')
  })

  it('点发布打开进度弹层（PublishModal 挂载并收到 app）', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('.stub-pub-modal').exists()).toBe(false)
    const btn = wrapper.findAll('.stub-card')[0].findAll('.stub-a-button')
      .find((b) => b.text() === '发布')
    await btn.trigger('click')
    expect(wrapper.find('.stub-pub-modal').exists()).toBe(true)
  })

  it('☰ 菜单「重建 dev」：弹确认框，确认后调 devReset（202 受理）', async () => {
    const wrapper = await mountView()
    const item = wrapper.findAll('.stub-card')[0].find('.zc-menu-reset')
    expect(item.exists()).toBe(true)
    await item.trigger('click')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    const opts = mocks.antd.Modal.confirm.mock.calls[0][0]
    await opts.onOk()
    expect(mocks.api.devReset).toHaveBeenCalledWith(1)
  })

  it('☰ 菜单「资源监控」：点击打开监控弹框（MetricsModal 挂载并收到当前应用）', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('.stub-metrics-modal').exists()).toBe(false)
    const item = wrapper.findAll('.stub-card')[0].find('.zc-menu-metrics')
    expect(item.exists()).toBe(true)
    await item.trigger('click')
    const modal = wrapper.find('.stub-metrics-modal')
    expect(modal.exists()).toBe(true)
    expect(modal.text()).toBe('1')   // 传进去的是第一张卡片的应用 id
    // 标题区按 status_prod 着色的状态徽标（卡片[0] prod=running）
    const title = wrapper.find('.zc-metrics-title')
    expect(title.text()).toContain('甲')
    expect(title.text()).toContain('运行中')
  })
})

// ---------- 预览地址：由后端 preview_url 给（前端硬拼 localhost 时，远程部署下那个地址
// 指向的是访问者自己的机器，端口倒是对的——错的是宿主） ----------
describe('AppsView 预览地址', () => {
  let writeText
  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(window.navigator, 'clipboard',
      { value: { writeText }, configurable: true })
  })

  it('「复制预览地址」写的是后端的 preview_url', async () => {
    const wrapper = await mountView()
    await wrapper.findAll('.zc-menu-copy')[0].trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('http://10.0.1.25:8301')
  })

  it('边界：http + IP 访问时「复制预览地址」也走降级（与正式地址同一个出口）', async () => {
    Object.defineProperty(window.navigator, 'clipboard',
      { value: undefined, configurable: true })
    const exec = stubExecCommand('http://10.0.1.25:8301')
    const wrapper = await mountView()

    await wrapper.findAll('.zc-menu-copy')[0].trigger('click')
    await flushPromises()

    expect(exec).toHaveBeenCalledWith('copy')
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已复制')
  })

  it('边界：预览抽屉里复制失败 → 地址摊在抽屉里，不跑到卡片上', async () => {
    Object.defineProperty(window.navigator, 'clipboard',
      { value: undefined, configurable: true })
    stubExecCommand('http://10.0.1.25:8301', () => false)
    mocks.api.devStatus.mockResolvedValue({ data: { status_dev: 'running', url: 'http://10.0.1.25:8301' } })
    const wrapper = await mountView()
    await wrapper.findAll('.zc-menu-preview')[0].trigger('click')
    await flushPromises()

    const drawer = wrapper.find('.stub-drawer')
    const copyBtn = drawer.findAll('.stub-a-button').find((b) => b.text().includes('复制地址'))
    await copyBtn.trigger('click')
    await flushPromises()

    expect(drawer.find('.zc-copy-manual').text()).toBe('http://10.0.1.25:8301')
    expect(wrapper.findAll('.stub-card')[0].find('.zc-copy-manual').exists()).toBe(false)
  })

  it('卡片「打开」链接指向 preview_url，而不是 localhost 拼端口', async () => {
    mocks.api.listApps.mockResolvedValue({ data: [{ ...APPS[0], status_dev: 'running' }] })
    const wrapper = mount(AppsView, { global: { stubs: STUBS } })
    await flushPromises()
    const link = wrapper.findAll('.stub-card')[0].findAll('a')
      .find((x) => x.text().includes('打开'))
    expect(link.attributes('href')).toBe('http://10.0.1.25:8301')
  })
})

// ---------- M3-a 商店接线：角标 / 发布到商店入口（最小追加，不动既有布局） ----------
const STORE_APPS = [
  { id: 1, name: '甲', slug: 'aaa', stack: 'vue3-fastapi', scale: 'small', dev_port: 8301,
    prod_port: 8401, status_dev: 'stopped', status_prod: 'running', current_version: 'b1' },
  { id: 2, name: '乙', slug: 'bbb', stack: 'vue3-fastapi', scale: 'small', dev_port: 8302,
    prod_port: 8402, status_dev: 'stopped', status_prod: 'stopped', current_version: null },
  { id: 3, name: '丙i', slug: 'ccci', stack: 'vue3-fastapi', scale: 'small', dev_port: 8303,
    prod_port: 8403, status_dev: 'stopped', status_prod: 'running', current_version: 'b1',
    store: { release_slug: 'aaa', release_version: 'b1', release_title: '甲', channel: 'managed' } },
  { id: 4, name: '丁', slug: 'ddd', stack: 'vue3-springboot', scale: 'large', dev_port: 8304,
    prod_port: 8404, status_dev: 'stopped', status_prod: 'running', current_version: 'b1' },
]

// 上架弹窗需要真一点的桩：open 才渲染、带 ok 按钮、输入框回写 v-model:value
const STORE_STUBS = {
  ...STUBS,
  'a-modal': { name: 'a-modal', props: ['open'], render() {
    if (!this.open) return null
    return h('div', { class: 'stub-modal' }, [
      this.$slots.default?.(),
      h('button', { class: 'stub-modal-ok', onClick: () => this.$emit('ok') }, 'ok')]) } },
  'a-input': { name: 'a-input', props: ['value'], render() {
    return h('input', { class: 'stub-input', value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
  'a-textarea': { name: 'a-textarea', props: ['value'], render() {
    return h('textarea', { class: 'stub-textarea', value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
}

async function mountStoreView() {
  mocks.api.listApps.mockResolvedValue({ data: STORE_APPS })
  const wrapper = mount(AppsView, { global: { stubs: STORE_STUBS } })
  await flushPromises()
  return wrapper
}

describe('AppsView 商店接线', () => {
  it('商店实例卡片带「来自商店」角标，普通应用没有', async () => {
    const wrapper = await mountStoreView()
    const cards = wrapper.findAll('.stub-card')
    expect(cards[2].text()).toContain('来自商店')
    expect(cards[0].text()).not.toContain('来自商店')
  })

  it('入口闸门：发布过的非商店应用 ☰ 菜单里有「发布到商店」；没发布过/商店实例没有', async () => {
    const wrapper = await mountStoreView()
    const has = (i) => wrapper.findAll('.stub-card')[i].find('.zc-menu-release').exists()
    expect(has(0)).toBe(true)
    expect(has(1)).toBe(false)   // current_version=null
    expect(has(2)).toBe(false)   // 商店实例不重复上架
    expect(has(3)).toBe(true)    // 大型栈（vue3-springboot）同样可上架
  })

  it('发布到商店：菜单项点开弹窗提交调 releaseToStore(appId, {title, summary})', async () => {
    mocks.api.releaseToStore.mockResolvedValue({ data: { id: 9 } })
    const wrapper = await mountStoreView()
    await wrapper.findAll('.stub-card')[0].find('.zc-menu-release').trigger('click')
    const modal = wrapper.find('.stub-modal')
    expect(modal.exists()).toBe(true)
    await modal.find('.stub-input').setValue('甲商店版')
    await modal.find('.stub-textarea').setValue('简介文本')
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(mocks.api.releaseToStore).toHaveBeenCalledWith(1, { title: '甲商店版', summary: '简介文本' })
  })
})

// ---------- 卡片右上 ☰ 折叠菜单：重命名/打开工程目录/复制预览地址/查看日志/删除 ----------
describe('AppsView 卡片折叠菜单', () => {
  it('卡片右上 ☰ 菜单收纳预览/重建/历史 + 五项工具操作', async () => {
    const wrapper = await mountView()
    const menu = wrapper.findAll('.stub-card')[0].find('.stub-dropdown')
    expect(menu.exists()).toBe(true)
    const text = menu.text()
    for (const label of ['预览', '重建 dev', '发布历史', '重命名', '打开工程目录', '复制预览地址', '查看日志', '删除应用'])
      expect(text).toContain(label)
  })

  it('☰ 菜单含 DB 操作入口且 href 指向 /apps/:id/db（新标签打开）', async () => {
    const wrapper = await mountView()
    const dbLink = wrapper.findAll('.stub-card')[0].find('.zc-menu-db')
    expect(dbLink.exists()).toBe(true)
    expect(dbLink.attributes('href')).toBe('/apps/1/db')
    expect(dbLink.attributes('target')).toBe('_blank')
    expect(dbLink.text()).toContain('DB 操作')
  })

  it('菜单点「删除应用」→ 弹确认框，确认后调 deleteApp', async () => {
    mocks.api.deleteApp.mockResolvedValue({ data: { kept: { root_path: 'apps/aaa' } } })
    mocks.api.listApps.mockResolvedValue({ data: APPS })
    const wrapper = mount(AppsView, { global: { stubs: STORE_STUBS } })
    await flushPromises()
    await wrapper.findAll('.zc-menu-del')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.stub-modal').exists()).toBe(true)
    await wrapper.find('.stub-modal-ok').trigger('click')
    await flushPromises()
    expect(mocks.api.deleteApp).toHaveBeenCalledWith(1)
  })
})

// ---------- 「打开工程目录」入口：与右栏文件面板共享同一裁决点（ADR-0009），
// 内置文件管理器从弹框改成新标签整页（ADR-0011） ----------
function stubOpen(ret = {}) {
  const open = vi.fn(() => ret)
  vi.stubGlobal('open', open)
  return open
}

async function clickFolder(wrapper, index = 0) {
  await wrapper.findAll('.zc-menu-folder')[index].trigger('click')
  await flushPromises()
}

describe('AppsView 打开工程目录', () => {
  it('auto + 本机访问：调起系统资源管理器，不开新标签', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    const open = stubOpen()
    mocks.api.openFolder.mockResolvedValue({ data: { ok: true } })
    const wrapper = await mountView()

    await clickFolder(wrapper)

    expect(mocks.api.openFolder).toHaveBeenCalledWith(11)
    expect(open).not.toHaveBeenCalled()
  })

  it('auto + 远端访问：新标签打开整页，完全不请求直开', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    const open = stubOpen()
    const wrapper = await mountView()

    await clickFolder(wrapper)

    expect(mocks.api.openFolder).not.toHaveBeenCalled()
    expect(open.mock.calls[0][1]).toBe('zerocode-files-11')
    expect(decodeURIComponent(open.mock.calls[0][0])).toBe('/workspaces/11/files?name=甲')
  })

  it('直开失败：降级到新标签整页并说明原因', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    const open = stubOpen()
    mocks.api.openFolder.mockRejectedValue({ response: { data: { detail: '找不到 xdg-open' } } })
    const wrapper = await mountView()

    await clickFolder(wrapper)

    expect(open).toHaveBeenCalled()
    expect(mocks.antd.message.info).toHaveBeenCalled()
  })

  it('系统参数强制 web：本机访问也直接开整页', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    mocks.cfg.form.openFolderMode = 'web'
    const open = stubOpen()
    const wrapper = await mountView()

    await clickFolder(wrapper)

    expect(mocks.api.openFolder).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalled()
  })

  it('边界：新标签被浏览器拦截（open 返回 null）时给出提示，不静默失败', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    stubOpen(null)
    const wrapper = await mountView()

    await clickFolder(wrapper)

    expect(mocks.antd.message.warning).toHaveBeenCalled()
  })

  it('边界：应用没有 workspace_id（脏数据）时不开一个必然 404 的标签', async () => {
    vi.stubGlobal('location', { hostname: '192.168.1.20' })
    const open = stubOpen()
    mocks.api.listApps.mockResolvedValue({ data: [{ ...APPS[0], workspace_id: null }] })
    const wrapper = mount(AppsView, { global: { stubs: STUBS } })
    await flushPromises()

    await clickFolder(wrapper)

    expect(open).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalled()
  })

  it('边界：连续双击只走一次直开请求（在途闸门）', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    let resolveOpen
    mocks.api.openFolder.mockReturnValue(new Promise((resolve) => { resolveOpen = resolve }))
    const wrapper = await mountView()

    await wrapper.findAll('.zc-menu-folder')[0].trigger('click')
    await wrapper.findAll('.zc-menu-folder')[0].trigger('click')
    resolveOpen({ data: { ok: true } })
    await flushPromises()

    expect(mocks.api.openFolder).toHaveBeenCalledTimes(1)
  })
})

// ---------- 卡片「正式地址」：域名的唯一事实来源是后端 app_urls（ADR-0016） ----------
// 前端补一个域名兜底看着无害，实际是两条故障：① 后端在 slug 缺位时**故意**回空串（不谎拼），
// 前端一兜就把这个刻意留白填成了假地址；② ensureUrls 静默吞异常，所以兜底恰好在「接口挂了」
// 时触发——用户点一下，剪贴板里是条死链，而它和真地址在界面上长得一模一样。
describe('AppsView 正式地址', () => {
  const prodLink = (wrapper, i = 0) => wrapper.findAll('.stub-card')[i].findAll('a')
    .find((x) => x.text().includes('正式地址'))

  let writeText
  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(window.navigator, 'clipboard',
      { value: { writeText }, configurable: true })
  })

  it('正常：后端回了 domain → 原样写剪贴板并提示已复制', async () => {
    mocks.api.appUrls.mockResolvedValue({ data: { domain: 'https://aaa.example.test' } })
    const wrapper = await mountView()

    await prodLink(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.api.appUrls).toHaveBeenCalledWith(1)
    expect(writeText).toHaveBeenCalledWith('https://aaa.example.test')
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已复制')
  })

  it('边界：appUrls 抛错（ensureUrls 静默）→ 不写剪贴板，改为显式提示', async () => {
    mocks.api.appUrls.mockRejectedValue(new Error('boom'))
    const wrapper = await mountView()

    await prodLink(wrapper).trigger('click')
    await flushPromises()

    expect(writeText).not.toHaveBeenCalled()
    expect(mocks.antd.message.success).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalled()
  })

  it('边界：后端故意回空串 domain（slug 缺位）→ 前端不补一个域名顶上', async () => {
    mocks.api.appUrls.mockResolvedValue({ data: { domain: '' } })
    const wrapper = await mountView()

    await prodLink(wrapper).trigger('click')
    await flushPromises()

    expect(writeText).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalled()
  })

  it('边界：响应里没有 domain 字段（后端换契约/脏数据）→ 同样不写剪贴板', async () => {
    mocks.api.appUrls.mockResolvedValue({ data: {} })
    const wrapper = await mountView()

    await prodLink(wrapper).trigger('click')
    await flushPromises()

    expect(writeText).not.toHaveBeenCalled()
    expect(mocks.antd.message.warning).toHaveBeenCalled()
  })

  it('边界：http + IP 访问控制台（navigator.clipboard 不存在）→ 降级 execCommand 仍能复制', async () => {
    // 非安全上下文（http://159.75.185.70:8100）里 navigator.clipboard 是 undefined，
    // 早先直接 .writeText 抛 TypeError、点了没反应；补 catch 后变成恒失败的「复制失败」。
    // 现在这条路要真的能复制成功（utils/clipboard.js 的降级）。
    Object.defineProperty(window.navigator, 'clipboard',
      { value: undefined, configurable: true })
    const exec = stubExecCommand('https://aaa.example.test')
    mocks.api.appUrls.mockResolvedValue({ data: { domain: 'https://aaa.example.test' } })
    const wrapper = await mountView()

    await prodLink(wrapper).trigger('click')
    await flushPromises()

    expect(exec).toHaveBeenCalledWith('copy')
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已复制')
    expect(mocks.antd.message.error).not.toHaveBeenCalled()
  })

  it('边界：剪贴板写入被拒（权限/窗口失焦）→ 降级成功后不谎报失败', async () => {
    writeText.mockRejectedValue(new Error('NotAllowedError'))
    const exec = stubExecCommand('https://aaa.example.test')
    mocks.api.appUrls.mockResolvedValue({ data: { domain: 'https://aaa.example.test' } })
    const wrapper = await mountView()

    await prodLink(wrapper).trigger('click')
    await flushPromises()

    expect(exec).toHaveBeenCalledWith('copy')
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已复制')
  })

  it('边界：两条路都不通 → 报错，并把完整地址就地显示成可选中文本', async () => {
    // 卡片上「正式地址」从头到尾没渲染过完整 URL（只有一个复制动作），所以 toast 里那句
    // 「请手动选择地址」在页面上根本没有对应物。兜底行就是把那个用户本来要拿到的东西摊开。
    Object.defineProperty(window.navigator, 'clipboard',
      { value: undefined, configurable: true })
    stubExecCommand('https://aaa.example.test', () => false)
    mocks.api.appUrls.mockResolvedValue({ data: { domain: 'https://aaa.example.test' } })
    const wrapper = await mountView()

    expect(wrapper.find('.zc-copy-manual').exists()).toBe(false)
    await prodLink(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.antd.message.error).toHaveBeenCalledWith('复制失败，请手动选择地址')
    expect(mocks.antd.message.success).not.toHaveBeenCalled()
    expect(wrapper.find('.zc-copy-manual').text()).toBe('https://aaa.example.test')
  })

  it('边界：兜底行只出现在失败的那张卡片上', async () => {
    Object.defineProperty(window.navigator, 'clipboard',
      { value: undefined, configurable: true })
    stubExecCommand('https://aaa.example.test', () => false)
    mocks.api.appUrls.mockResolvedValue({ data: { domain: 'https://aaa.example.test' } })
    const wrapper = await mountView()

    await prodLink(wrapper, 0).trigger('click')
    await flushPromises()

    const cards = wrapper.findAll('.stub-card')
    expect(cards[0].find('.zc-copy-manual').exists()).toBe(true)
    expect(cards[1].find('.zc-copy-manual').exists()).toBe(false)
  })

  it('取不到地址时不缓存空结果：下一次点击会重新问后端（可重试）', async () => {
    mocks.api.appUrls.mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ data: { domain: 'https://aaa.example.test' } })
    const wrapper = await mountView()

    await prodLink(wrapper).trigger('click')
    await flushPromises()
    await prodLink(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.api.appUrls).toHaveBeenCalledTimes(2)
    expect(writeText).toHaveBeenCalledWith('https://aaa.example.test')
  })
})
