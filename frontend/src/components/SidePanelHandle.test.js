// @vitest-environment happy-dom
// 侧栏边缘手柄的交互契约：左右方向、收起态位置/图标/文案、点击与拖拽事件。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SidePanelHandle from './SidePanelHandle.vue'

function mountHandle(props = {}) {
  return mount(SidePanelHandle, {
    props: { side: 'left', edge: 260, ...props },
  })
}

describe('SidePanelHandle', () => {
  it('左栏展开：贴左边界，钮文案为「收起」，图标朝左，带 6px 拖拽热区', () => {
    const wrapper = mountHandle()
    const handle = wrapper.find('.zc-edge')
    expect(handle.classes()).toContain('zc-edge-left')
    expect(handle.classes()).not.toContain('is-collapsed')
    expect(handle.element.style.left).toBe('260px')

    const toggle = wrapper.find('.zc-edge-toggle')
    expect(toggle.attributes('aria-label')).toBe('收起左侧栏')
    expect(wrapper.find('.anticon-left').exists()).toBe(true)

    const strip = wrapper.find('.zc-edge-drag')
    expect(strip.attributes('role')).toBe('separator')
    expect(strip.attributes('aria-orientation')).toBe('vertical')
    expect(strip.attributes('aria-label')).toContain('双击恢复默认')
  })

  it('左栏收起：手柄贴视口左缘，钮文案为「展开」，图标朝右，拖拽热区消失', () => {
    const wrapper = mountHandle({ collapsed: true })
    const handle = wrapper.find('.zc-edge')
    expect(handle.classes()).toContain('is-collapsed')
    expect(handle.element.style.left).toBe('0px')
    expect(wrapper.find('.zc-edge-toggle').attributes('aria-label')).toBe('展开左侧栏')
    expect(wrapper.find('.anticon-right').exists()).toBe(true)
    expect(wrapper.find('.zc-edge-drag').exists()).toBe(false)
  })

  it('右栏展开：贴右边界，钮文案为「收起」，图标朝右', () => {
    const wrapper = mountHandle({ side: 'right', edge: 300 })
    const handle = wrapper.find('.zc-edge')
    expect(handle.classes()).toContain('zc-edge-right')
    expect(handle.element.style.right).toBe('300px')
    expect(handle.element.style.left).toBe('')
    expect(wrapper.find('.zc-edge-toggle').attributes('aria-label')).toBe('收起右侧栏')
    expect(wrapper.find('.anticon-right').exists()).toBe(true)
  })

  it('右栏收起：钮文案为「展开」，图标朝左', () => {
    const wrapper = mountHandle({ side: 'right', edge: 0, collapsed: true })
    expect(wrapper.find('.zc-edge-toggle').attributes('aria-label')).toBe('展开右侧栏')
    expect(wrapper.find('.anticon-left').exists()).toBe(true)
  })

  it('点钮冒泡 toggle；拖拽热区按下冒泡 dragstart，双击冒泡 reset', async () => {
    const wrapper = mountHandle()
    await wrapper.find('.zc-edge-toggle').trigger('click')
    expect(wrapper.emitted('toggle')).toHaveLength(1)

    await wrapper.find('.zc-edge-drag').trigger('pointerdown')
    expect(wrapper.emitted('dragstart')).toHaveLength(1)
    expect(wrapper.emitted('dragstart')[0][0]).toBeTruthy()

    await wrapper.find('.zc-edge-drag').trigger('dblclick')
    expect(wrapper.emitted('reset')).toHaveLength(1)
  })

  it('拖拽中带 is-dragging 类，供高亮竖线与关闭过渡使用', () => {
    const wrapper = mountHandle({ dragging: true })
    expect(wrapper.find('.zc-edge').classes()).toContain('is-dragging')
  })
})
