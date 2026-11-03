// @vitest-environment happy-dom
import { describe, it, expect } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'
import PageInspectCard from './PageInspectCard.vue'

const ImageStub = {
  name: 'a-image',
  props: ['src', 'alt', 'width'],
  render() {
    return h('img', { class: 'stub-image', src: this.src })
  },
}

function mountCard(result) {
  return mount(PageInspectCard, {
    props: { msg: { name: 'preview_page', result } },
    global: { stubs: { 'a-image': ImageStub } },
  })
}

const base = {
  ok: true,
  app: { slug: 'demo', stack: 'vue3-springboot' },
  status: 'running',
  url: 'http://localhost:8241/orders',
  title: '订单管理',
  text: '订单列表',
  console: [],
  page_errors: [],
  failed_requests: [],
  screenshot: null,
  vision: { supported: false, note: '当前会话模型不支持视觉，已跳过看图诊断' },
  redacted: 0,
  truncated: false,
  checks_left: 5,
}

describe('preview_page 身份徽标（ADR-0038）', () => {
  it('admin 身份显示徽标', () => {
    const w = mountCard({ ...base, identity: { kind: 'admin', label: '以 admin 身份验证', note: '' } })
    const badge = w.find('.zc-page-identity')
    expect(badge.exists()).toBe(true)
    expect(badge.classes()).toContain('is-admin')
    expect(badge.text()).toBe('以 admin 身份验证')
    expect(w.text()).not.toContain('身份：')
  })

  it('匿名身份显示徽标与原因', () => {
    const w = mountCard({
      ...base, identity: { kind: 'anonymous', label: '匿名（未登录）', note: '该应用没有可用的登录接口' },
    })
    const badge = w.find('.zc-page-identity')
    expect(badge.classes()).toContain('is-anonymous')
    expect(badge.text()).toBe('匿名（未登录）')
    expect(badge.attributes('title')).toBe('该应用没有可用的登录接口')
    expect(w.text()).toContain('身份：该应用没有可用的登录接口')
  })

  it('旧结果没有 identity 时不画徽标', () => {
    const w = mountCard({ ...base })
    expect(w.find('.zc-page-identity').exists()).toBe(false)
  })

  it('未执行检查（kind=none）时不画徽标', () => {
    const w = mountCard({ ...base, ok: false, status: 'stopped', identity: { kind: 'none' } })
    expect(w.find('.zc-page-identity').exists()).toBe(false)
  })
})
