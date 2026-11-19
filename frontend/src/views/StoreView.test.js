// @vitest-environment happy-dom
// Task 12 + Ticket 008：商店页三视图与登录态身份接线；审批收件箱只对创始人（平台管理员）可见。
// 真 Antd 插件挂载（与 AppsView 的桩件模式不同：商店页无 WS/进度流，交互全在 tabs/modal 上）。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import StoreView from './StoreView.vue'
import * as api from '../api'

// happy-dom 没有 ResizeObserver，antd Tabs 的 ink bar 测量会直接抛
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

vi.mock('../api')
// 页头新增 BackButton（内部 useRouter()）：本测试只挂 Antd 无 router，这里给一个 push 桩。
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const releases = [
  { id: 1, slug: 'test1', version: 'b2', title: '陌如玉', summary: '桌面备忘', stack: 'vue3-fastapi',
    publisher: '作者', created_by: 1, installed: false, installed_apps: [], category: '数据管理',
    install_count: 3, screenshots: [], trial_available: true, trial_url: 'http://localhost:18301' },
  { id: 2, slug: 'test2', version: 'b1', title: '图库', summary: '相册', stack: 'vue3-fastapi',
    publisher: '作者', created_by: 1, installed: false, installed_apps: [], category: '内容展示',
    install_count: 0, screenshots: [], trial_available: false, trial_url: '' },
]
const applications = [
  { id: 9, release_id: 1, want_name: '我的备忘', status: 'pending', title: '陌如玉', version: 'b2' },
]

function mountView () {
  return mount(StoreView, { global: { plugins: [Antd] }, attachTo: document.body })
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  api.authMe.mockResolvedValue({ data: { id: 1, nickname: '我' } })
  api.storeReleases.mockResolvedValue({ data: releases })
  api.storeApplications.mockResolvedValue({ data: applications })
  api.storeMyReleases.mockResolvedValue({ data: [] })
})

describe('StoreView', () => {
  it('渲染商店卡片：标题/发布者/版本与申请按钮', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('陌如玉')
    expect(w.text()).toContain('作者')
    expect(w.findAll('button').some((b) => b.text().includes('申请'))).toBe(true)
  })

  it('已安装的卡片不再给申请按钮，而是给出实例入口', async () => {
    api.storeReleases.mockResolvedValue({ data: [{ ...releases[0], installed: true,
      installed_apps: [{ app_id: 7, name: '我的备忘', status_prod: 'running' }] }] })
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('我的备忘')
    expect(w.findAll('button').some((b) => b.text().includes('申请'))).toBe(false)
  })

  it('卡片展示分类标签与安装量', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('数据管理')
    expect(w.text()).toContain('安装 3 次')
  })

  it('按分类筛选：点分类标签带参重取，列表随之收窄', async () => {
    const w = mountView()
    await flushPromises()
    api.storeReleases.mockResolvedValueOnce({ data: [releases[0]] })
    await w.find('[data-test="cat-数据管理"]').trigger('click')
    await flushPromises()
    expect(api.storeReleases).toHaveBeenLastCalledWith({ q: '', category: '数据管理', sort: 'latest' })
    expect(w.text()).toContain('陌如玉')
    expect(w.text()).not.toContain('图库')
  })

  it('搜索：点搜索按钮带 q 重取', async () => {
    const w = mountView()
    await flushPromises()
    api.storeReleases.mockResolvedValueOnce({ data: [releases[1]] })
    await w.find('[data-test="store-search"]').setValue('图库')
    await w.find('[data-test="store-search-btn"]').trigger('click')
    await flushPromises()
    expect(api.storeReleases).toHaveBeenLastCalledWith({ q: '图库', category: '', sort: 'latest' })
    expect(w.text()).toContain('图库')
    expect(w.text()).not.toContain('陌如玉')
  })

  it('排序：切到「安装量」带参重取', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="sort-installs"]').trigger('click')
    await flushPromises()
    expect(api.storeReleases).toHaveBeenLastCalledWith({ q: '', category: '', sort: 'installs' })
  })

  it('分页：每页 20 条，第二页显示余量', async () => {
    const many = Array.from({ length: 25 }, (_, i) =>
      ({ ...releases[0], id: i + 1, title: `应用${i + 1}` }))
    api.storeReleases.mockResolvedValue({ data: many })
    const w = mountView()
    await flushPromises()
    expect(w.findAll('.zc-card').length).toBe(20)
    await w.find('.ant-pagination-item-2').trigger('click')
    await flushPromises()
    expect(w.findAll('.zc-card').length).toBe(5)
    expect(w.text()).toContain('应用25')
  })

  it('无截图显示占位封面，有截图显示首图', async () => {
    api.storeReleases.mockResolvedValue({ data: [
      { ...releases[0], screenshots: [] },
      { ...releases[1], screenshots: ['/api/store/releases/2/screenshots/0'] }] })
    const w = mountView()
    await flushPromises()
    expect(w.findAll('.zc-cover-placeholder').length).toBe(1)
    expect(w.find('.zc-cover-img').attributes('src')).toBe('/api/store/releases/2/screenshots/0')
    w.unmount()
  })

  it('点卡片封面打开详情抽屉：标题/简介/元数据可见', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('.zc-cover').trigger('click')
    await flushPromises()
    const drawer = document.querySelector('.ant-drawer-content')
    expect(drawer).toBeTruthy()
    expect(drawer.textContent).toContain('陌如玉')
    expect(drawer.textContent).toContain('桌面备忘')
    expect(drawer.textContent).toContain('安装 3 次')
    w.unmount()
  })

  it('抽屉截图轮播可切换', async () => {
    api.storeReleases.mockResolvedValue({ data: [
      { ...releases[0], screenshots: ['/s/0', '/s/1'] }] })
    const w = mountView()
    await flushPromises()
    await w.find('.zc-cover').trigger('click')
    await flushPromises()
    expect(document.querySelector('.zc-shot-img').getAttribute('src')).toBe('/s/0')
    document.querySelector('[data-test="shot-next"]').click()
    await flushPromises()
    expect(document.querySelector('.zc-shot-img').getAttribute('src')).toBe('/s/1')
    w.unmount()
  })

  it('作者可见补传/删图入口并可上传；非作者不可见', async () => {
    api.storeAddShot.mockResolvedValue({ data: { screenshots: ['/s/new'] } })
    const w = mountView()
    await flushPromises()
    await w.find('.zc-cover').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="shot-add"]')).toBeTruthy()
    const file = new File([new Uint8Array([137, 80, 78, 71])], 'a.png', { type: 'image/png' })
    const input = document.querySelector('[data-test="shot-file"]')
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    input.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(api.storeAddShot).toHaveBeenCalledWith(1, file)
    w.unmount()

    api.storeReleases.mockResolvedValue({ data: [{ ...releases[0], created_by: 999 }] })
    const w2 = mountView()
    await flushPromises()
    await w2.find('.zc-cover').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="shot-add"]')).toBeFalsy()
    w2.unmount()
  })

  it('在线试用：dev 在跑可点并新标签打开试用地址；停了置灰', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    try {
      const w = mountView()
      await flushPromises()
      const btns = w.findAll('[data-test="trial-btn"]')
      expect(btns[0].attributes('disabled')).toBeUndefined()
      expect(btns[1].attributes('disabled')).toBeDefined()
      await btns[0].trigger('click')
      expect(openSpy).toHaveBeenCalledWith('http://localhost:18301', '_blank')
      w.unmount()
    } finally {
      openSpy.mockRestore()
    }
  })

  it('试用告知：详情抽屉写明试用写入作者测试数据库，且无任何代启入口', async () => {
    const w = mountView()
    await flushPromises()
    await w.findAll('.zc-cover')[1].trigger('click')   // 图库：dev 停止
    await flushPromises()
    const drawer = document.querySelector('.ant-drawer-content')
    expect(drawer.textContent).toContain('作者的测试环境')
    expect(drawer.textContent).toContain('测试数据库')
    const trialBtn = drawer.querySelector('[data-test="trial-btn"]')
    expect(trialBtn.disabled).toBe(true)
    expect(drawer.textContent).not.toContain('启动预览')
    w.unmount()
  })

  it('我的申请：pending 行可撤回，已决行没有撤回按钮', async () => {
    api.storeApplications.mockImplementation((box) => Promise.resolve({
      data: box === 'mine' ? [...applications,
        { id: 10, release_id: 1, want_name: '旧的', status: 'rejected', title: '陌如玉', version: 'b2' }] : [],
    }))
    api.storeCancel.mockResolvedValue({ data: { status: 'cancelled' } })
    const w = mountView()
    await flushPromises()
    const mineTab = w.findAll('.ant-tabs-tab').find((t) => t.text().includes('我的申请'))
    await mineTab.trigger('click')
    await flushPromises()
    const btns = w.findAll('[data-test="cancel-apply"]')
    expect(btns.length).toBe(1)
    await btns[0].trigger('click')
    await flushPromises()
    expect(api.storeCancel).toHaveBeenCalledWith(9)
    w.unmount()
  })

  it('已装实例打开走 prod_url 新标签；prod 停止置灰', async () => {
    api.storeReleases.mockResolvedValue({ data: [{ ...releases[0], installed: true,
      installed_apps: [
        { app_id: 7, name: '我的备忘', status_prod: 'running', prod_url: 'https://x.example.com' },
        { app_id: 8, name: '停了', status_prod: 'stopped', prod_url: 'https://y.example.com' }] }] })
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    try {
      const w = mountView()
      await flushPromises()
      const btns = w.findAll('button')
      const openBtn = btns.find((b) => b.text().includes('打开 我的备忘'))
      const stoppedBtn = btns.find((b) => b.text().includes('打开 停了'))
      expect(stoppedBtn.attributes('disabled')).toBeDefined()
      await openBtn.trigger('click')
      expect(openSpy).toHaveBeenCalledWith('https://x.example.com', '_blank')
      expect(w.html()).not.toContain('localhost:')
      w.unmount()
    } finally {
      openSpy.mockRestore()
    }
  })

  it('已登录：页头不再显示昵称（身份入口已上全局顶栏），也没有昵称弹层', async () => {    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="me-nickname"]').exists()).toBe(false)
    expect(w.find('[data-test="actor-modal"]').exists()).toBe(false)
  })

  it('未登录（authMe 401）引导去登录页，不再弹昵称框', async () => {
    api.authMe.mockRejectedValue({ response: { status: 401 } })
    const assign = vi.fn()
    const saved = window.location
    // happy-dom 的 location 不可直接改写：换整对象，测完还原
    Object.defineProperty(window, 'location', { value: { assign }, writable: true, configurable: true })
    try {
      const w = mountView()
      await flushPromises()
      expect(assign).toHaveBeenCalledWith('/login')
      expect(w.find('[data-test="actor-modal"]').exists()).toBe(false)
    } finally {
      Object.defineProperty(window, 'location', { value: saved, writable: true, configurable: true })
    }
  })

  it('非创始人看不到审批收件箱（审批权收口创始人）', async () => {
    api.authMe.mockResolvedValue({ data: { id: 1, nickname: '普通用户', is_founder: false } })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="tab-review"]').exists()).toBe(false)
  })

  it('创始人看到审批收件箱并可决策', async () => {
    api.authMe.mockResolvedValue({ data: { id: 1, nickname: '管理员', is_founder: true } })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="tab-review"]').exists()).toBe(true)
  })

  it('审批：驳回必须填原因才可调接口', async () => {
    api.authMe.mockResolvedValue({ data: { id: 1, nickname: '管理员', is_founder: true } })
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="tab-review"]').trigger('click')
    await flushPromises()
    await w.find('[data-test="reject-btn"]').trigger('click')
    await flushPromises()
    expect(api.storeReject).not.toHaveBeenCalled()
    await w.find('[data-test="reject-reason"]').setValue('名字不合适')
    await w.find('[data-test="reject-confirm"]').trigger('click')
    await flushPromises()
    expect(api.storeReject).toHaveBeenCalledWith(9, '名字不合适')
  })
})
