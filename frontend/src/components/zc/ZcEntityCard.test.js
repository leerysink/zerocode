// @vitest-environment happy-dom
// 卡片骨架薄件契约：可选槽缺位就不该产生节点、状态轨只在传色时出现、封面不可点时不发事件。
// 等高与两行截断是内建行为，由 CSS 承担（happy-dom 不做布局，样式在 011 的目视走查里验收）。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import ZcEntityCard from './ZcEntityCard.vue'

function mountCard(props = {}, slots = {}) {
  return mount(ZcEntityCard, { props, slots })
}

describe('ZcEntityCard', () => {
  const all = {
    cover: '<img alt="" src="shot.png" />',
    badge: '<span class="pill">已上架</span>',
    title: '测试应用一',
    tags: '<span>vue3-node</span>',
    menu: '<button class="menu">☰</button>',
    default: '<div class="band">仪表带</div>',
    summary: '一段简介',
    meta: '创始人 · 3 次安装',
    actions: '<button>申请使用</button>',
    footer: '<span>底部条</span>',
  }

  it('全槽齐活时按节奏渲染', () => {
    const w = mountCard({}, all)
    expect(w.find('[data-test="entity-title"]').text()).toBe('测试应用一')
    expect(w.find('[data-test="entity-summary"]').text()).toBe('一段简介')
    expect(w.find('.zc-entity-badge .pill').exists()).toBe(true)
    expect(w.find('.band').exists()).toBe(true)
    expect(w.find('.zc-entity-meta').text()).toContain('3 次安装')
    expect(w.find('.zc-entity-actions button').text()).toBe('申请使用')
    expect(w.find('.zc-entity-footer').text()).toBe('底部条')
  })

  it('未提供的可选槽不产生空壳节点', () => {
    const w = mountCard({}, { title: '只有标题' })
    for (const sel of ['.zc-entity-cover', '.zc-entity-tags', '.zc-entity-menu',
                       '.zc-entity-summary', '.zc-entity-meta', '.zc-entity-actions',
                       '.zc-entity-footer']) {
      expect(w.find(sel).exists(), sel).toBe(false)
    }
  })

  it('rail 传色才注状态轨变量，未传不写内联 style', () => {
    expect(mountCard({ rail: 'red' }).attributes('style')).toContain('--zc-rail: red')
    expect(mountCard({}).attributes('style')).toBeUndefined()
  })

  it('muted 给整卡降饱和类', () => {
    expect(mountCard({ muted: true }).classes()).toContain('is-muted')
  })

  it('封面：不可点时忽略点击，可点时发 cover-click', async () => {
    const silent = mountCard({}, { cover: '<div class="c" />' })
    await silent.find('[data-test="entity-cover"]').trigger('click')
    expect(silent.emitted('cover-click')).toBeFalsy()

    const w = mountCard({ coverClickable: true }, { cover: '<div class="c" />' })
    expect(w.find('[data-test="entity-cover"]').classes()).toContain('is-clickable')
    await w.find('[data-test="entity-cover"]').trigger('click')
    expect(w.emitted('cover-click')).toHaveLength(1)
  })
})
