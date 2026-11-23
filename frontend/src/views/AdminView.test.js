// @vitest-environment happy-dom
// 账号治理台：管理员门禁、账号名册读数、封禁（带理由）与解封、本人不可封。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia, setActivePinia } from 'pinia'
import AdminView from './AdminView.vue'
import { useAuthStore } from '../stores/auth'
import * as api from '../api'

global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

vi.mock('../api')

// 页头薄件里的 BackButton 顶层调 useRouter()：本测试不建真 router，给桩避免注入告警
const mocks = vi.hoisted(() => ({ router: { push: vi.fn() } }))
vi.mock('vue-router', () => ({ useRouter: () => mocks.router }))

const accounts = [
  { id: 1, nickname: '创始人', is_founder: true, created_at: '2026-11-23T02:00:00',
    banned_at: null, banned_reason: null, apps_used: 3, apps_limit: null, stacks_running: 1, stacks_limit: null },
  { id: 2, nickname: '小明', is_founder: false, created_at: '2026-11-23T03:00:00',
    banned_at: null, banned_reason: null, apps_used: 2, apps_limit: 10, stacks_running: 0, stacks_limit: 4 },
  { id: 3, nickname: '滥用者', is_founder: false, created_at: '2026-11-23T04:00:00',
    banned_at: '2026-11-23T05:00:00', banned_reason: '占满端口池',
    apps_used: 12, apps_limit: 10, stacks_running: 5, stacks_limit: 4 },
]

function mountView (user = { id: 1, is_founder: true }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = user
  return mount(AdminView, { global: { plugins: [Antd, pinia] }, attachTo: document.body })
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  api.adminAccounts.mockResolvedValue({ data: accounts })
  api.adminBan.mockResolvedValue({ data: accounts[1] })
  api.adminUnban.mockResolvedValue({ data: accounts[1] })
  api.adminConfig.mockResolvedValue({ data: {} })
  api.adminInvites.mockResolvedValue({ data: [
    { id: 1, code_masked: '····a1b2', status: 'unused', created_at: '2026-11-23T06:00:00',
      used_at: null, used_by_nickname: null },
    { id: 2, code_masked: '····c3d4', status: 'used', created_at: '2026-11-23T06:10:00',
      used_at: '2026-11-23T07:00:00', used_by_nickname: '小明' },
  ] })
  api.adminIssueInvite.mockResolvedValue({ data: { id: 3, code: 'plain-new-code' } })
  api.adminRevokeInvite.mockResolvedValue({ data: { id: 1, status: 'revoked' } })
  api.adminPutConfig.mockResolvedValue({ data: { ok: true } })
})

describe('AdminView', () => {
  it('非管理员看不到治理台，也不发名册请求（视图层第一道门禁）', async () => {
    const w = mountView({ id: 9, is_founder: false })
    await flushPromises()
    expect(w.find('[data-test="admin-denied"]').exists()).toBe(true)
    expect(w.find('[data-test="admin-head"]').exists()).toBe(false)
    expect(api.adminAccounts).not.toHaveBeenCalled()
  })

  it('管理员进页面拉到名册，身份/状态按行渲染，被封原因可见', async () => {
    const w = mountView()
    await flushPromises()
    expect(api.adminAccounts).toHaveBeenCalledTimes(1)
    const text = w.text()
    expect(text).toContain('创始人')
    expect(text).toContain('小明')
    expect(text).toContain('占满端口池')
    expect(w.find('[data-test="page-stat-已封禁"]').text()).toContain('1')
    // 被封的那行出「解封」，正常的那行出「封禁」
    expect(w.find('[data-test="acct-ban-小明"]').exists()).toBe(true)
    expect(w.find('[data-test="acct-unban"]').exists()).toBe(true)
  })

  it('本人那行只给禁用的「本人」，不给封禁按钮（自封 = 锁死平台）', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="acct-self"]').exists()).toBe(true)
    expect(w.find('[data-test="acct-ban-创始人"]').exists()).toBe(false)
  })

  it('封禁走确认框并带上理由，成功后重拉名册', async () => {
    const w = mountView()
    await flushPromises()
    api.adminAccounts.mockClear()

    await w.find('[data-test="acct-ban-小明"]').trigger('click')
    await flushPromises()
    // a-modal 传送到 body，VTU 的 w.find 够不着——沿用 StoreAdminView.test.js 的取法
    const modal = document.querySelector('.ant-modal-content')
    expect(modal.textContent).toContain('小明')
    expect(modal.textContent).toContain('所有设备立即下线')

    const reason = modal.querySelector('[data-test="ban-reason"]')
    reason.value = '连续占满端口池'
    reason.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()

    modal.querySelector('.ant-modal-footer .ant-btn-primary').click()
    await flushPromises()

    expect(api.adminBan).toHaveBeenCalledWith(2,
      { reason: '连续占满端口池', stop_stacks: false })
    expect(api.adminAccounts).toHaveBeenCalledTimes(1)
  })

  it('封禁默认不停栈；勾上才带 stop_stacks=true，并当场说明会中断什么', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="acct-ban-小明"]').trigger('click')
    await flushPromises()
    const modal = document.querySelector('.ant-modal-content')
    const box = modal.querySelector('[data-test="ban-stop-stacks"]')
    expect(box.checked).toBe(false)
    expect(modal.textContent).not.toContain('中断')   // 没勾就不该吓唬人

    box.click()
    await flushPromises()
    expect(document.querySelector('.ant-modal-content').textContent).toContain('中断')

    document.querySelector('.ant-modal-footer .ant-btn-primary').click()
    await flushPromises()
    expect(api.adminBan).toHaveBeenCalledWith(2, { reason: '', stop_stacks: true })
  })

  it('解封直调端点，不需要理由', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="acct-unban"]').trigger('click')
    await flushPromises()
    expect(api.adminUnban).toHaveBeenCalledWith(3)
  })

  it('平台默认：未改动时保存按钮禁用；只提交改动过的那一个键', async () => {
    api.adminConfig.mockResolvedValue({ data: {
      'quota.max_apps': 10, 'quota.max_running_stacks': 4,
      'llm.base_url': 'https://p.example/v1', 'llm.model': 'p-model', 'llm.api_key': 'sk-***' } })
    api.adminPutConfig.mockResolvedValue({ data: { ok: true } })
    const w = mountView()
    await flushPromises()

    expect(w.find('[data-test="def-save"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="admin-defaults"]').text()).toContain('全平台共用')

    const model = w.find('[data-test="def-model"]')
    expect(model.element.value).toBe('p-model')
    await model.setValue('new-model')
    expect(w.find('[data-test="def-save"]').attributes('disabled')).toBeUndefined()

    await w.find('[data-test="def-save"]').trigger('click')
    await flushPromises()
    // 只发 diff：全量提交会把没动的字段按当前读数写回去，等于顺手覆盖了别人的改动
    expect(api.adminPutConfig).toHaveBeenCalledWith({ 'llm.model': 'new-model' })
  })

  it('名册拉不到时不渲染成「谁都没被封」的假象', async () => {
    api.adminAccounts.mockRejectedValue(new Error('boom'))
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="admin-head"]').exists()).toBe(true)
    // 收窄到名册表：邀请码区里合法地会出现「小明」（那是用者列），整页文本判不出名册是否空
    expect(w.find('[data-test="admin-accounts"]').text()).not.toContain('小明')
    expect(w.find('[data-test="page-stat-账号"]').text()).toContain('0')
  })

  it('用量与上限同排显示；管理员那行写「不限（管理员）」而不是一个假数字', async () => {
    api.adminAccounts.mockResolvedValue({ data: [
      { id: 1, nickname: '创始人', is_founder: true, created_at: '2026-11-23T02:00:00',
        banned_at: null, banned_reason: null,
        apps_used: 42, apps_limit: null, stacks_running: 9, stacks_limit: null },
      { id: 2, nickname: '小明', is_founder: false, created_at: '2026-11-23T03:00:00',
        banned_at: null, banned_reason: null,
        apps_used: 11, apps_limit: 10, stacks_running: 2, stacks_limit: 4 },
    ] })
    const w = mountView()
    await flushPromises()
    const cells = w.findAll('[data-test="acct-apps"]').map((c) => c.text())
    expect(cells).toContain('42 / 不限（管理员）')
    expect(cells).toContain('11 / 10')
    // 超额只标红，不禁任何操作：小明的封禁按钮仍在
    expect(w.find('[data-test="acct-ban-小明"]').exists()).toBe(true)
    expect(w.findAll('.zc-over').length).toBeGreaterThan(0)
  })

  it('额度弹层：留空提交 null（不限），填 0 提交 0（一个都不许）', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="acct-quota-小明"]').trigger('click')
    await flushPromises()
    const modal = document.querySelector('.ant-modal-content')
    expect(modal.textContent).toContain('不会')
    expect(modal.textContent).toContain('管理员自己不受')

    const stacks = modal.querySelector('[data-test="quota-max-stacks"]')
    stacks.value = '0'
    stacks.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    modal.querySelector('.ant-modal-footer .ant-btn-primary').click()
    await flushPromises()

    expect(api.adminPutQuota).toHaveBeenCalledWith(2,
      { max_apps: 10, max_running_stacks: 0 })
  })
})

describe('AdminView 邀请码（ADR-0069）', () => {
  it('邀请码：列表只出掩码，明文只在签发弹层里露面一次', async () => {
    const w = mountView()
    await flushPromises()
    const panel = w.find('[data-test="admin-invites"]')
    expect(panel.text()).toContain('····a1b2')
    expect(panel.text()).toContain('未使用')
    expect(panel.text()).toContain('小明')
    expect(panel.text()).not.toContain('plain-new-code')

    await w.find('[data-test="invite-issue"]').trigger('click')
    await flushPromises()
    expect(api.adminIssueInvite).toHaveBeenCalledTimes(1)
    // a-modal 传送到 body：取 .ant-modal-content（对齐 StoreAdminView.test.js 的口径）
    const reveal = [...document.querySelectorAll('.ant-modal-content')]
      .find((m) => m.textContent.includes('只在这里出现一次'))
    expect(reveal).toBeTruthy()
    expect(reveal.querySelector('[data-test="invite-plain"]').value).toBe('plain-new-code')
  })

  it('邀请码：只有未使用的行给作废按钮，作废后重拉列表', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="invite-revoke-1"]').exists()).toBe(true)
    expect(w.find('[data-test="invite-revoke-2"]').exists()).toBe(false)

    api.adminInvites.mockClear()
    await w.find('[data-test="invite-revoke-1"]').trigger('click')
    await flushPromises()
    expect(api.adminRevokeInvite).toHaveBeenCalledWith(1)
    expect(api.adminInvites).toHaveBeenCalledTimes(1)
  })

  it('非管理员不发邀请码请求（治理面三重读口一起被视图挡住）', async () => {
    const w = mountView({ id: 9, is_founder: false })
    await flushPromises()
    expect(api.adminInvites).not.toHaveBeenCalled()
    expect(api.adminConfig).not.toHaveBeenCalled()
  })
})
