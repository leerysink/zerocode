// @vitest-environment happy-dom
// 顶栏账户区契约（自 SideBar 页脚迁移至此，位置改为页面右上角常驻顶栏）：
// 昵称回显读 /api/auth/me 单源；登出走 api.authLogout 后整页跳登录页；
// authMe 401（被踢/会话过期）兜底引导回登录页。api 层全部 mock，只测组件行为。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  authMe: vi.fn(), authLogout: vi.fn(),
}))
vi.mock('ant-design-vue', () => ({ message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

import { authMe, authLogout } from '../api'
import AccountBar from './AccountBar.vue'

const stubs = { 'a-layout-header': { name: 'a-layout-header', render() { return h('header', this.$slots.default?.()) } } }

async function mountBar(meResp) {
  setActivePinia(createPinia())
  if (meResp instanceof Error) authMe.mockRejectedValue(meResp)
  else authMe.mockResolvedValue({ data: meResp })
  const wrapper = mount(AccountBar, { global: { plugins: [createPinia()], stubs } })
  await flushPromises()
  return wrapper
}

// happy-dom 的 location.assign 不可直接改，按项目惯例整桩替换 + 还原
function stubLocation() {
  const assign = vi.fn()
  const saved = window.location
  Object.defineProperty(window, 'location', { value: { assign }, writable: true, configurable: true })
  return { assign, restore: () => Object.defineProperty(window, 'location', { value: saved, writable: true, configurable: true }) }
}

describe('AccountBar 右上角账户区', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('顶栏显示当前账号昵称', async () => {
    const wrapper = await mountBar({ id: 1, nickname: '小明' })
    expect(authMe).toHaveBeenCalled()
    expect(wrapper.find('[data-test="account-nickname"]').text()).toContain('小明')
  })

  it('点登出：调接口后整页跳登录页（登出后 cookie 已作废，留在原地只会处处 401）', async () => {
    const { assign, restore } = stubLocation()
    try {
      authLogout.mockResolvedValue({})
      const wrapper = await mountBar({ id: 1, nickname: '小明' })
      await wrapper.find('[data-test="logout-btn"]').trigger('click')
      await flushPromises()
      expect(authLogout).toHaveBeenCalled()
      expect(assign).toHaveBeenCalledWith('/login')
    } finally {
      restore()
    }
  })

  it('服务端本就无会话时登出接口报错，也照样跳登录页', async () => {
    const { assign, restore } = stubLocation()
    try {
      authLogout.mockRejectedValue(new Error('401'))
      const wrapper = await mountBar({ id: 1, nickname: '小明' })
      await wrapper.find('[data-test="logout-btn"]').trigger('click')
      await flushPromises()
      expect(assign).toHaveBeenCalledWith('/login')
    } finally {
      restore()
    }
  })

  it('authMe 返回 401（被踢/过期）：兜底引导回登录页', async () => {
    const { assign, restore } = stubLocation()
    try {
      const wrapper = await mountBar(Object.assign(new Error('unauthorized'), { response: { status: 401 } }))
      await flushPromises()
      expect(wrapper.find('[data-test="account-nickname"]').text()).toBe('')
      expect(assign).toHaveBeenCalledWith('/login')
    } finally {
      restore()
    }
  })
})
