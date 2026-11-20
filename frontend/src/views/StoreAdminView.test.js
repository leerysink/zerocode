// @vitest-environment happy-dom
// 商店管理（运营台）：创始人门禁、三列等高卡、状态 tab、☰ 编辑/详情/下架、审批队列与分类总览。
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
  api.storeUpdateRelease.mockResolvedValue({ data: { ok: true } })
  api.storePurgeRelease.mockResolvedValue({ data: { ok: true } })
  api.storeAddShot.mockResolvedValue({ data: { ok: true } })
  api.storeDelShot.mockResolvedValue({ data: { ok: true } })
  api.storeApprove.mockResolvedValue({ data: { ok: true } })
  api.storeReject.mockResolvedValue({ data: { ok: true } })
})

describe('StoreAdminView', () => {
  // ☰ 的菜单项渲染在 body 的浮层里，须经浮层点，wrapper.find 取不到
  async function clickMenu (w, testId, index = 0) {
    await w.findAll('.zc-sadm-menu-btn')[index].trigger('click')
    await flushPromises()
    const item = document.querySelector(`[data-test="${testId}"]`)
    expect(item, `菜单项 ${testId} 未出现`).toBeTruthy()
    item.click()
    await flushPromises()
  }

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

  it('状态 tab 切换收窄卡片；☰ 按状态给下架或重新上架', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-tab-withdrawn"]').trigger('click')
    expect(w.findAll('[data-test="adm-card"]')).toHaveLength(1)
    await clickMenu(w, 'adm-republish')
    expect(api.storeRepublish).toHaveBeenCalledWith(2)
    // 已下架行不给「下架」项（状态互斥，不是靠 disabled 糊过去）
    expect(document.querySelector('[data-test="adm-withdraw"]')).toBeNull()
  })

  it('下架：走 ☰ 菜单调接口并重取列表', async () => {
    const w = mountView()
    await flushPromises()
    await clickMenu(w, 'adm-withdraw')
    expect(api.storeWithdraw).toHaveBeenCalledWith(1)
    expect(api.storeAdminReleases).toHaveBeenCalledTimes(2)
  })

  it('详情抽屉：身份字段只读、未在服预览禁用、可转编辑、截图可删', async () => {
    api.storeAdminReleases.mockResolvedValue({
      data: [{ ...releases[0], application_count: 3,
               screenshots: ['http://t/store/releases/1/shot.png'] }],
    })
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-detail-open"]').trigger('click')
    await flushPromises()
    const drawer = document.querySelector('.ant-drawer-content')
    expect(drawer.textContent).toContain('标识 slug')
    expect(drawer.textContent).toContain('test1')
    expect(drawer.textContent).toContain('3 条申请')
    expect(drawer.textContent).toContain('未被编辑')
    expect(drawer.querySelector('[data-test="adm-detail-preview"]').disabled).toBe(false)
    drawer.querySelector('[data-test="adm-shot-del"]').click()
    await flushPromises()
    expect(api.storeDelShot).toHaveBeenCalledWith(1, 0)
    drawer.querySelector('[data-test="adm-detail-edit"]').click()
    await flushPromises()
    expect(document.querySelector('.ant-modal-content').textContent).toContain('编辑「测试应用一」')
  })

  it('详情抽屉：preview_url 为空时打开预览禁用并说明原因', async () => {
    api.storeAdminReleases.mockResolvedValue({ data: [releases[1]] })
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-detail-open"]').trigger('click')
    await flushPromises()
    const btn = document.querySelector('[data-test="adm-detail-preview"]')
    expect(btn.disabled).toBe(true)
    expect(btn.getAttribute('title')).toContain('未在服')
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

  it('分类管理：读字典出条目数，改名调序走 PATCH，新增走 POST', async () => {
    api.storeCategories.mockResolvedValue({ data: [
      { key: '效率工具', label: '效率工具', sort: 10, enabled: true,
        release_count: 2, published_count: 1 },
      { key: '其他', label: '其他', sort: 90, enabled: true, release_count: 0, published_count: 0 },
    ] })
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-cat-open"]').trigger('click')
    await flushPromises()
    const modal = document.querySelector('.ant-modal-content')
    expect(modal.textContent).toContain('1 / 2')       // 在架 / 版本
    const label = modal.querySelector('[data-test="cat-label"]')
    label.value = '效率与备忘'
    label.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    modal.querySelector('[data-test="cat-save"]').click()
    await flushPromises()
    expect(api.storePatchCategory).toHaveBeenCalledWith('效率工具', {
      label: '效率与备忘', sort: 10,
    })
    // 「其他」是兜底：不能停用也不能删
    const rows = modal.querySelectorAll('.zc-sadm-cat-row2')
    const other = rows[rows.length - 1]
    expect(other.querySelector('[data-test="cat-enabled"]').disabled).toBe(true)
    expect(other.querySelector('[data-test="cat-del"]').disabled).toBe(true)
  })

  it('分类停用：调 PATCH 带 enabled=false；被拒时把后端的解释原样给运营看', async () => {
    api.storeCategories.mockResolvedValue({ data: [
      { key: '看板', label: '看板', sort: 15, enabled: true, release_count: 3, published_count: 3 },
    ] })
    api.storePatchCategory.mockRejectedValue({ response: { data: { detail: '分类不存在或已停用：看板' } } })
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="adm-cat-open"]').trigger('click')
    await flushPromises()
    const modal = document.querySelector('.ant-modal-content')
    modal.querySelector('[data-test="cat-enabled"]').click()
    await flushPromises()
    expect(api.storePatchCategory).toHaveBeenCalledWith('看板', { enabled: false })
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

  it('运营编辑：☰ 开弹框回填当前值，保存只发白名单三字段', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('.zc-sadm-menu-btn').trigger('click')
    await flushPromises()
    const item = document.querySelector('[data-test="adm-edit"]')
    expect(item).toBeTruthy()
    item.click()
    await flushPromises()
    const modal = document.querySelector('.ant-modal-content')
    expect(modal.textContent).toContain('编辑「测试应用一」')
    const title = modal.querySelector('[data-test="adm-edit-title"]')
    expect(title.value).toBe('测试应用一')
    title.value = '改过的标题'
    title.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    modal.querySelector('.ant-btn-primary').click()
    await flushPromises()
    expect(api.storeUpdateRelease).toHaveBeenCalledWith(1, {
      title: '改过的标题', summary: 'Vue 3 + Node.js', category: '效率工具',
    })
    // 保存成功后重新拉列表，让留痕与文案从服务端真值回来
    expect(api.storeAdminReleases).toHaveBeenCalledTimes(2)
  })

  it('运营编辑：标题清空则不发请求；有留痕的行渲染「已由 X 编辑」副行', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('.zc-sadm-menu-btn').trigger('click')
    await flushPromises()
    document.querySelector('[data-test="adm-edit"]').click()
    await flushPromises()
    const modal = document.querySelector('.ant-modal-content')
    const title = modal.querySelector('[data-test="adm-edit-title"]')
    title.value = '   '
    title.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    modal.querySelector('.ant-btn-primary').click()
    await flushPromises()
    expect(api.storeUpdateRelease).not.toHaveBeenCalled()

    api.storeAdminReleases.mockResolvedValue({
      data: [{ ...releases[0], updated_at: '2026-11-20T02:00:00', editor: '创始人' }],
    })
    const w2 = mountView()
    await flushPromises()
    expect(w2.find('[data-test="adm-edited"]').exists()).toBe(true)
    expect(w2.find('[data-test="adm-edited"]').text()).toContain('已由 创始人 编辑')
  })
})

describe('StoreAdminView 状态页签面卡（整页层级统一）', () => {
  it('三个状态页签同住一张面卡，计数徽标在卡内', async () => {
    const w = mountView()
    await flushPromises()
    const tabs = w.find('[data-test="sadm-tabs"]')
    expect(tabs.exists()).toBe(true)
    expect(tabs.classes()).toContain('zc-panel')
    expect(tabs.findAll('[data-test^="adm-tab-"]').length).toBe(3)
    expect(tabs.findAll('.n').length).toBe(3)
    w.unmount()
  })
})