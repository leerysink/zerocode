// @vitest-environment happy-dom
// 登录页提交链路契约（Ticket 010 冒烟发现的真实回归）：
// antd Form 在无 :model 时永不触发 finish——登录/注册按钮点了没任何反应。
// 本测试走全真实 antd 组件（不 stub a-form），断言「填表→submit→auth store 被调用」
// 这条端到端链路通，把 :model 绑定钉死。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import Antd from 'ant-design-vue'

vi.mock('../api', () => ({
  authLogin: vi.fn(), authRegister: vi.fn(), authLogout: vi.fn(), authMe: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

import { authLogin } from '../api'
import LoginView from '../views/LoginView.vue'
import { useAuthStore } from '../stores/auth'

// 用真实 DOM 事件驱动（native submit 走 antd Form 的完整校验/finish 链路）
function mountLogin() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(LoginView)
  app.use(createPinia())
  app.use(Antd)
  app.mount(host)
  return { host, app }
}

async function inputInto(host, index, value) {
  const input = host.querySelectorAll('input')[index]
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

async function submitForm(host) {
  const form = host.querySelector('form')
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  // antd 校验是异步 promise 链，多刷几轮
  for (let i = 0; i < 6; i++) await nextTick()
  await new Promise((r) => setTimeout(r, 0))
  for (let i = 0; i < 6; i++) await nextTick()
}

describe('LoginView 提交链路', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    authLogin.mockReset().mockResolvedValue({ data: { id: 1, nickname: 'uimoke925a' } })
  })

  it('登录模式：填昵称口令后提交，auth.login 被真实调用', async () => {
    const { host, app } = mountLogin()
    await inputInto(host, 0, 'uimoke925a')
    await inputInto(host, 1, 'smoke-pwd-123')
    await submitForm(host)
    expect(authLogin).toHaveBeenCalledWith({ nickname: 'uimoke925a', password: 'smoke-pwd-123' })
    expect(useAuthStore().user?.nickname).toBe('uimoke925a')
    app.unmount()
  })
})
