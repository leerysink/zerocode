// @vitest-environment happy-dom
// 页头返回按钮契约：文案「返回」+ 点击固定导航到对话页（不走浏览器历史，避免直达 /apps 时点了没反应）。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

import BackButton from './BackButton.vue'

// a-button 用原生 button 桩：@click 监听经 attr 继承落在根元素上，trigger('click') 即等价真实点击
function mountBack(props = {}) {
  return mount(BackButton, {
    props,
    global: { stubs: { 'a-button': { template: '<button><slot /></button>' } } },
  })
}

beforeEach(() => vi.clearAllMocks())

describe('BackButton', () => {
  it('显示「返回」文案', () => {
    expect(mountBack().find('button').text()).toContain('返回')
  })

  it('点击后跳对话页 /', async () => {
    await mountBack().find('button').trigger('click')
    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith('/')
  })

  it('可用 to 覆盖目标路由', async () => {
    await mountBack({ to: '/settings' }).find('button').trigger('click')
    expect(push).toHaveBeenCalledWith('/settings')
  })
})
