// @vitest-environment happy-dom
// 商店管理页：创始人门禁、三列卡片、状态 tab、下架/重新上架/预览、审批队列与分类总览。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia, setActivePinia } from 'pinia'
import StoreAdminView from './StoreAdminView.vue'
import { useAuthStore } from '../stores/auth'
import * as api from '../api'

global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

vi.mock('../api')

// 页头薄件里的 BackButton 顶层调 useRouter()：本测试不建真 router，给桩避免注入告警
// （对齐 SkillsView.test.js / StoreView.test.js 的口径）
const mocks = vi.hoisted(() => ({ router: { push: vi.fn() } }))
vi.mock('vue-router', () => ({ useRouter: () => mocks.router }))

const releases = [
  { id: 1, slug: 'test1', version: 'v0.1.0', title: '测试应用一', summary: 'Vue 3 + Node.js',
    stack: 'vue3-node', category: '效率工具', status: 'published', publisher: '创始人',
    install_count: 0, created_at: '2026-11-20T00:00:00', screenshots: [], preview_url: 'http://localhost:18401' },
  { id: 2, slug: 'test1', version: 'v0.1.0', title: '测试应用一', summary: 'Vue 3 + Node.js',
    stack: 'vue3-node', category: '效率工具', status: 'withdrawn', publisher: '创始人',
    install_count: 0, created_at: '2026-11-20T00:00:00', screenshots: [], preview_url: '' },
]
const review = [
  { id: 9, release_id: 1, want_name: '小明的实例', status: 'pending', applicant: '小明',
    title: '测试应用一', version: 'v0.1.0', created_at: '2026-11-20T00:00:00' },
]

function mountView (user = { id: 1, is_founder: true }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = user
  return mount(StoreAdminView, { global: { plugins: [Antd, pinia] }, attachTo: document.body })
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  api.storeAdminReleases.mockResolvedValue({ data: releases })
  api.storeApplications.mockResolvedValue({ data: review })
  api.storeWithdraw.mockResolvedValue({ data: { ok: true } })
  api.storeRepublish.mockResolvedValue({ data: { ok: true } })
  api.storeApprove.mockResolvedValue({ data: { ok: true } })
  api.storeReject.mockResolvedValue({ data: { ok: true } })
})

describe('StoreAdminView', () => {
  it('非创始人：门禁空态，不拉管理列表', async () => {
    const w = mountView({ id: 2, is_founder: false })
    await flushPromises()
    expect(w.text()).toContain('商店管理仅创始人可见')
    expect(api.storeAdminReleases).not.toHaveBeenCalled()
  })

  it('创始人：卡片渲染标题/状态胶囊/标签/安装量，tab 带计数', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.findAll('[data-test="adm-card"]')).toHaveLength(2)
    expect(w.text()).toContain('测试应用一')
    expect(w.text()).toContain('已上架')
    expect(w.text()).toContain('已下架')
    expect(w.text()).toContain('效率工具')
    expect(w.text()).toContain('0 次安装')
    expect(w.find('[data-test="adm-tab-all"]').text()).toContain('2')
    expect(w.find('[data-test="adm-tab-published"]').text()).toContain('1')
    expect(w.find('[data-test="adm-tab-withdrawn"]').text()).toContain('1')
  })

  it('状态 tab 切换收窄卡片', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-tab-withdrawn"]').trigger('click')
    expect(w.findAll('[data-test="adm-card"]')).toHaveLength(1)
    expect(w.find('[data-test="adm-republish"]').exists()).toBe(true)
    expect(w.find('[data-test="adm-withdraw"]').exists()).toBe(false)
  })

  it('下架调接口并重取；重新上架同理', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-withdraw"]').trigger('click')
    await flushPromises()
    expect(api.storeWithdraw).toHaveBeenCalledWith(1)
    await w.find('[data-test="adm-tab-withdrawn"]').trigger('click')
    await w.find('[data-test="adm-republish"]').trigger('click')
    await flushPromises()
    expect(api.storeRepublish).toHaveBeenCalledWith(2)
  })

  it('预览：有地址开窗，无地址禁用', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const w = mountView()
    await flushPromises()
    const btns = w.findAll('[data-test="adm-preview"]')
    expect(btns[1].attributes('disabled')).toBeDefined()
    await btns[0].trigger('click')
    expect(open).toHaveBeenCalledWith('http://localhost:18401', '_blank')
    open.mockRestore()
  })

  it('审批队列：通过调 approve；驳回先验原因', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-review-open"]').trigger('click')
    await flushPromises()
    const drawer = document.querySelector('.ant-drawer-content')
    expect(drawer.textContent).toContain('小明的实例')
    drawer.querySelector('[data-test="adm-reject"]').click()
    await flushPromises()
    expect(api.storeReject).not.toHaveBeenCalled()
    const reason = drawer.querySelector('[data-test="adm-reject-reason"]')
    reason.value = '名字不合适'
    reason.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    drawer.querySelector('[data-test="adm-reject"]').click()
    await flushPromises()
    expect(api.storeReject).toHaveBeenCalledWith(9, '名字不合适')
    drawer.querySelector('[data-test="adm-approve"]').click()
    await flushPromises()
    expect(api.storeApprove).toHaveBeenCalledWith(9)
  })

  it('分类管理：固定枚举的在架/版本总览', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-cat-open"]').trigger('click')
    await flushPromises()
    const modal = document.querySelector('.ant-modal-content')
    expect(modal.textContent).toContain('1 在架 / 2 版本')
    expect(modal.textContent).toContain('平台固定枚举')
  })

  it('页头：返回固定回对话页，统计格按当前数据出数', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.find('.zc-back').exists()).toBe(true)
    await w.find('.zc-back').trigger('click')
    expect(mocks.router.push).toHaveBeenCalledWith('/')
    expect(mocks.router.push).not.toHaveBeenCalledWith('/store')
    expect(w.find('[data-test="page-stat-all"] b').text()).toBe('2')
    expect(w.find('[data-test="page-stat-published"] b').text()).toBe('1')
    expect(w.find('[data-test="page-stat-withdrawn"] b').text()).toBe('1')
    expect(w.find('[data-test="page-stat-review"] b').text()).toBe('1')
  })

  it('加载与空态由 ZcEmptyState 承担：不混排 spin 与 empty', async () => {
    api.storeAdminReleases.mockImplementation(() => new Promise(() => {}))
    const w = mountView()
    // loading 在 onMounted 里翻转，重渲染排在下一个 tick
    await w.vm.$nextTick()
    expect(w.find('[data-test="empty-loading"]').exists()).toBe(true)
    expect(w.find('[data-test="empty-state"]').exists()).toBe(false)

    api.storeAdminReleases.mockResolvedValue({ data: [] })
    api.storeApplications.mockResolvedValue({ data: [] })
    const w2 = mountView()
    await flushPromises()
    expect(w2.find('[data-test="empty-loading"]').exists()).toBe(false)
    expect(w2.find('[data-test="empty-state"]').text()).toContain('还没有应用上架')
  })
})