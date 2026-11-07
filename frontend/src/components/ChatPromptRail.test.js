// @vitest-environment happy-dom
// 提问导航条的展示层：一根横杠一条提问、位置按盒内像素摆、当前视口那条加亮、
// 悬停浮层给出提问全文、点击把 index 发出去。几何与高亮由 promptRailModel 算好，这里只验渲染。
import { describe, it, expect } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'
import ChatPromptRail from './ChatPromptRail.vue'

// tooltip 桩：把触发区与 title 浮层都渲出来，否则 antd 的悬浮层懒渲染、测不到「悬停看得到内容」
const TooltipStub = {
  name: 'a-tooltip',
  render() {
    return h('span', { class: 'stub-tooltip' }, [
      h('span', { class: 'stub-tip-title' }, this.$slots.title ? this.$slots.title() : []),
      ...(this.$slots.default ? this.$slots.default() : []),
    ])
  },
}

const MARKS = [
  { index: 0, label: '请帮我全面分析电投能源（002128）的基本面', top: 88, active: false },
  { index: 3, label: '电投能源近期操作建议', top: 100, active: true },
  { index: 7, label: '卡住几个小时了？你不会自主停下再启动', top: 112, active: false },
]

const mountRail = (marks = MARKS) =>
  mount(ChatPromptRail, {
    props: { marks },
    global: { stubs: { 'a-tooltip': TooltipStub } },
  })

it('一条提问一根横杠，按盒内像素位置成簇摆放', () => {
  const w = mountRail()
  const items = w.findAll('.zc-rail-mark')
  expect(items.length).toBe(3)
  expect(items.map((n) => n.attributes('style'))).toEqual([
    'top: 88px;', 'top: 100px;', 'top: 112px;',
  ])
})

it('轨道是固定高度的居中盒，不随聊天区拉满', () => {
  const w = mountRail()
  expect(w.find('.zc-rail').attributes('style')).toContain('height: 200px;')
})

it('横杠按提问顺序渲染，index 落在锚点属性上供定位', () => {
  const w = mountRail()
  expect(w.findAll('.zc-rail-mark').map((n) => n.attributes('data-prompt-mark'))).toEqual(['0', '3', '7'])
})

it('视口内那条加亮，其余保持常态', () => {
  const w = mountRail()
  const items = w.findAll('.zc-rail-mark')
  expect(items[1].classes()).toContain('is-active')
  expect(items[0].classes()).not.toContain('is-active')
  expect(items[2].classes()).not.toContain('is-active')
})

it('悬停浮层里是提问原文，不是截断后的片段', () => {
  const w = mountRail()
  const tips = w.findAll('.stub-tip-title')
  expect(tips[0].text()).toBe('请帮我全面分析电投能源（002128）的基本面')
  expect(tips[2].text()).toBe('卡住几个小时了？你不会自主停下再启动')
})

it('点击横杠把该条提问的 index 发出去', async () => {
  const w = mountRail()
  await w.findAll('.zc-rail-mark')[2].trigger('click')
  expect(w.emitted('select')).toEqual([[7]])
})

it('横杠可键盘聚焦，无障碍名就是提问内容', () => {
  const w = mountRail()
  const first = w.findAll('.zc-rail-mark')[0]
  expect(first.attributes('aria-label')).toBe('请帮我全面分析电投能源（002128）的基本面')
  expect(first.attributes('type')).toBe('button')
})

it('没有横杠时轨道不画任何标记（是否整条隐藏由调用方判定）', () => {
  const w = mountRail([])
  expect(w.findAll('.zc-rail-mark').length).toBe(0)
})
