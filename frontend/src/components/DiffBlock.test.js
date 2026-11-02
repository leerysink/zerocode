// @vitest-environment happy-dom
// DiffBlock 的「查看具体」入口：只在拿得到记录 id 时才出现，点击按收口后的地址开新标签。
// 头行过滤那条契约在 tests/diffblock.test.js（SSR 渲染断言），这里只管按钮这一件事。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

import { message } from 'ant-design-vue'
import DiffBlock from './DiffBlock.vue'

const DIFF = '--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n'

function mountBlock(props = {}) {
  return mount(DiffBlock, { props: { diff: DIFF, path: 'x', action: 'edit', ...props } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('DiffBlock 查看具体', () => {
  it('给了会话与记录 id 才出现按钮，点击按收口的地址开新标签', () => {
    const open = vi.fn(() => ({}))
    vi.stubGlobal('open', open)

    const wrapper = mountBlock({ sessionId: '12', changeId: '345' })
    const btn = wrapper.find('.zc-diff-open')

    expect(btn.exists()).toBe(true)
    expect(btn.text()).toBe('查看具体')
    btn.trigger('click')

    expect(open).toHaveBeenCalledWith('/sessions/12/changes/345', 'zerocode-change-345')
  })

  it('边界：没有记录 id（存量历史消息挂的 diff）时不渲染按钮，不给点了没反应的入口', () => {
    const open = vi.fn(() => ({}))
    vi.stubGlobal('open', open)

    for (const changeId of [undefined, null, '', 'abc', ' ', '1.5', '-7']) {
      const wrapper = mountBlock({ sessionId: '12', changeId })
      expect(wrapper.find('.zc-diff-open').exists()).toBe(false)
    }
    expect(open).not.toHaveBeenCalled()
  })

  it('边界：只有记录 id 却没有会话 id 时同样不渲染（详情接口挂在会话下）', () => {
    const wrapper = mountBlock({ changeId: '345' })

    expect(wrapper.find('.zc-diff-open').exists()).toBe(false)
  })

  it('数字 id 与字符串 id 一样能开（列表接口给的是 BIGINT，地址按字符串拼）', () => {
    const open = vi.fn(() => ({}))
    vi.stubGlobal('open', open)

    const wrapper = mountBlock({ sessionId: 12, changeId: 345 })
    wrapper.find('.zc-diff-open').trigger('click')

    expect(open).toHaveBeenCalledWith('/sessions/12/changes/345', 'zerocode-change-345')
  })

  it('被浏览器拦弹窗时如实提示，不让用户以为已经打开', async () => {
    vi.stubGlobal('open', vi.fn(() => null))

    const wrapper = mountBlock({ sessionId: '12', changeId: '345' })
    await wrapper.find('.zc-diff-open').trigger('click')

    expect(message.error).toHaveBeenCalled()
  })
})
