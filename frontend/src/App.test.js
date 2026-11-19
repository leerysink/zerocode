// @vitest-environment happy-dom
// 全局账户顶栏的渲染条件契约：/login（meta.public）不挂 AccountBar，
// 登录页保持全屏无干扰；其余所有页面一律挂载（应用/商店/设置/聊天共用一个身份入口）。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'

vi.mock('./api', () => ({
  authMe: vi.fn().mockResolvedValue({ data: { id: 1, nickname: '小明' } }),
  authLogout: vi.fn(),
}))
vi.mock('ant-design-vue', () => ({
  default: { name: 'AConfigProvider', props: ['theme', 'locale'], render() { return h('div', this.$slots.default?.()) } },   // a-config-provider
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  theme: { darkAlgorithm: { __sentinel: 'dark' } },
}))

// 可切换 meta 的假路由：useRoute() 返回的对象按用例改 meta.public / matched
const fakeRoute = { meta: {}, path: '/', matched: [{}] }
vi.mock('vue-router', () => ({
  useRoute: () => fakeRoute,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

import App from './App.vue'
import Antd, { theme as antdTheme } from 'ant-design-vue'
import { CONSOLE_THEME_KEY } from './utils/theme'

function mountApp() {
  return mount(App, {
    global: {
      plugins: [createPinia()],
      // mock 的 default 就是 provider 桩：显式注册，模板里的 a-config-provider 才解析得到
      components: { 'a-config-provider': Antd },
      stubs: {
        // router-view 桩：只关心 App 壳层挂不挂 AccountBar，页面组件无关
        RouterView: { name: 'RouterView', render: () => h('div', { class: 'stub-view' }) },
      },
    },
  })
}

describe('App 壳层账户顶栏', () => {
  beforeEach(() => {
    fakeRoute.meta = {}
    fakeRoute.path = '/'
    fakeRoute.matched = [{}]
  })

  it('业务页面（非 public）挂 AccountBar 顶栏', () => {
    const wrapper = mountApp()
    expect(wrapper.findComponent({ name: 'AccountBar' }).exists()).toBe(true)
  })

  it('登录页（meta.public）不挂顶栏，全屏无干扰', () => {
    fakeRoute.meta = { public: true }
    const wrapper = mountApp()
    expect(wrapper.findComponent({ name: 'AccountBar' }).exists()).toBe(false)
    expect(wrapper.find('.stub-view').exists()).toBe(true) // 路由出口本身照常渲染
  })

  it('首帧导航未解析完（matched 空）不挂顶栏，免得在登录页闪一下', () => {
    fakeRoute.matched = []
    const wrapper = mountApp()
    expect(wrapper.findComponent({ name: 'AccountBar' }).exists()).toBe(false)
  })

  it('深色偏好启用 darkAlgorithm，浅色不启用；主色两种主题保持不变', () => {
    localStorage.setItem(CONSOLE_THEME_KEY, 'dark')
    const dark = mountApp()
    const darkCfg = dark.findComponent({ name: 'AConfigProvider' }).props('theme')
    expect(darkCfg.algorithm).toBe(antdTheme.darkAlgorithm)
    expect(darkCfg.token.colorPrimary).toBe('#5b7a9d')

    localStorage.clear()
    const light = mountApp()
    const lightCfg = light.findComponent({ name: 'AConfigProvider' }).props('theme')
    expect(lightCfg.algorithm).toBeUndefined()
    expect(lightCfg.token.colorPrimary).toBe('#5b7a9d')
  })
})
