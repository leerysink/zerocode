// @vitest-environment happy-dom
// 空态薄件契约：loading 只出 spin、不出 empty；空态文案由调用点给（两态互斥是这件的全部职责）。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import ZcEmptyState from './ZcEmptyState.vue'

const stubs = {
  'a-spin': { template: '<div class="stub-spin" />' },
  'a-empty': { template: '<div class="stub-empty">{{ $attrs.description }}</div>' },
}

const mountState = (props = {}) => mount(ZcEmptyState, { props, global: { stubs } })

describe('ZcEmptyState', () => {
  it('加载中只渲染 spin', () => {
    const w = mountState({ loading: true })
    expect(w.find('[data-test="empty-loading"]').exists()).toBe(true)
    expect(w.find('[data-test="empty-state"]').exists()).toBe(false)
  })

  it('非加载渲染空态并透传文案', () => {
    const w = mountState({ description: '还没有应用上架' })
    expect(w.find('[data-test="empty-state"]').text()).toBe('还没有应用上架')
  })

  it('文案有默认值', () => {
    expect(mountState().find('[data-test="empty-state"]').text()).toBe('暂无数据')
  })
})
