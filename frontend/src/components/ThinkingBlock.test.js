// @vitest-environment happy-dom
// 思考块的折叠契约：默认一律折叠（流式也不例外），点开才见正文；
// 展开意图要回吐给父层，好让收口后的历史块接住它（ADR-0065）。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ThinkingBlock from './ThinkingBlock.vue'

const TEXT = '先看看仓库结构，再决定改哪一层。'

const mountBlock = (props) => mount(ThinkingBlock, { props: { thinking: TEXT, ...props } })
// v-show 的收起态是内联样式，happy-dom 读不出 CSS 类效果，只能这样判（同 SideBar.test.js）
const collapsed = (w) => (w.find('.zc-think-body').attributes('style') || '').includes('display: none')

describe('ThinkingBlock 默认折叠', () => {
  it('流式进行中默认折叠，头部只给「思考中…」', () => {
    const w = mountBlock({ streaming: true })
    expect(collapsed(w)).toBe(true)
    expect(w.find('.zc-think-head').text()).toContain('思考中…')
  })

  it('历史形态默认折叠，头部是「查看思考」', () => {
    const w = mountBlock({})
    expect(collapsed(w)).toBe(true)
    expect(w.find('.zc-think-head').text()).toContain('查看思考')
  })

  it('点头部展开正文，再点收回', async () => {
    const w = mountBlock({ streaming: true })
    await w.find('.zc-think-head').trigger('click')
    expect(collapsed(w)).toBe(false)
    expect(w.find('.zc-think-body').text()).toBe(TEXT)
    await w.find('.zc-think-head').trigger('click')
    expect(collapsed(w)).toBe(true)
  })

  it('展开意图回吐给父层，收回也回吐', async () => {
    const w = mountBlock({ streaming: true })
    await w.find('.zc-think-head').trigger('click')
    expect(w.emitted('update:open').at(-1)).toEqual([true])
    await w.find('.zc-think-head').trigger('click')
    expect(w.emitted('update:open').at(-1)).toEqual([false])
  })

  it('父层交出展开态（收口接住的那条）时直接是展开的', () => {
    expect(collapsed(mountBlock({ open: true }))).toBe(false)
  })

  it('流式收口成历史形态时不重新折叠：展开态由父层决定，不由 streaming 决定', async () => {
    const w = mountBlock({ streaming: true })
    await w.find('.zc-think-head').trigger('click')
    await w.setProps({ streaming: false })
    expect(collapsed(w)).toBe(false)
  })
})
