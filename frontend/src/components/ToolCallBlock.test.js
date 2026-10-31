// @vitest-environment happy-dom
// 工具调用块的人话标题：clarify_done 那句「我对需求的理解是…」必须进标题——
// 自声明豁免的全部价值就在于它对用户可见（ADR-0032）。其余工具照旧 `🔧 工具名`。
import { describe, it, expect } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'

import ToolCallBlock from './ToolCallBlock.vue'

const CollapseStub = {
  name: 'a-collapse',
  render() { return h('div', { class: 'stub-collapse' }, this.$slots.default?.()) },
}
const PanelStub = {
  name: 'a-collapse-panel',
  props: ['header'],
  render() {
    return h('div', { class: 'stub-panel' }, [
      h('span', { class: 'stub-panel-header' }, this.header),
      this.$slots.default?.(),
    ])
  },
}

function headerOf(msg) {
  const w = mount(ToolCallBlock, {
    props: { msg },
    global: { stubs: { 'a-collapse': CollapseStub, 'a-collapse-panel': PanelStub } },
  })
  return w.find('.stub-panel-header').text()
}

describe('ToolCallBlock 的澄清收口标题', () => {
  it('已对齐：标题带出理解原文', () => {
    const header = headerOf({
      name: 'clarify_done',
      args: { understanding: '给首页加一个导出按钮' },
      result: { ok: true, basis: 'clarified', asked: 2, understanding: '给首页加一个导出按钮' },
    })
    expect(header).toContain('需求已对齐')
    expect(header).toContain('给首页加一个导出按钮')
    expect(header).not.toContain('未提问')
  })

  it('豁免直通：标明这次没提问就直接开工', () => {
    const header = headerOf({
      name: 'clarify_done',
      args: {},
      result: { ok: true, basis: 'already_clear', asked: 0, understanding: '把 README 标题改成 X' },
    })
    expect(header).toContain('未提问，直接开工')
  })

  it('收口没过：不假装成功', () => {
    const header = headerOf({
      name: 'clarify_done',
      args: {},
      result: { ok: false, error: 'clarify_done 必须给出 understanding' },
    })
    expect(header).toContain('澄清收口未通过')
    expect(header).not.toContain('需求已对齐')
  })

  it('结果还没回来（待答态）：退回工具名，不编出标题', () => {
    expect(headerOf({ name: 'clarify_done', args: {}, result: null })).toBe('🔧 clarify_done')
  })

  it('其余工具不受影响', () => {
    expect(headerOf({ name: 'write_file', args: { path: 'a.txt' }, result: { ok: true } }))
      .toBe('🔧 write_file')
  })
})
