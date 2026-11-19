// @vitest-environment happy-dom
// web_fetch 结果卡（ADR-0053 联网读取）：外部页面内容一律按纯文本与带 rel 的锚点渲染。
// 重点不是「画出来了」，而是元信息、额度与失败措辞都说的是事实。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import WebFetchCard from './WebFetchCard.vue'

function mountCard(msg, progress = null) {
  return mount(WebFetchCard, { props: { msg, progress } })
}

const okResult = {
  ok: true,
  url: 'https://example.com/docs?x=1',
  status: 200,
  content_type: 'text/html; charset=utf-8',
  bytes: 2048,
  title: '示例站',
  description: '示例描述',
  text: '正文第一行\n正文第二行',
  links: [
    { text: '详情', url: 'https://example.com/a' },
    { text: '', url: 'https://example.com/b' },
  ],
  redirects: ['https://example.com/'],
  elapsed_ms: 120,
  fetches_left: 9,
  redacted: 0,
  truncated: false,
}

describe('WebFetchCard', () => {
  it('成功读取：标题、页面标题、描述、正文与元信息都在', () => {
    const w = mountCard({ name: 'web_fetch', result: okResult })
    expect(w.find('.zc-page').classes()).toContain('is-ok')
    expect(w.text()).toContain('网页已读取')
    expect(w.text()).toContain('示例站')
    expect(w.text()).toContain('示例描述')
    expect(w.text()).toContain('正文第一行')
    expect(w.text()).toContain('HTTP 200')
    expect(w.text()).toContain('跳转 1 次')
    expect(w.text()).toContain('还能抓 9 个页面')
  })

  it('「打开原页」链接带 noopener noreferrer', () => {
    const w = mountCard({ name: 'web_fetch', result: okResult })
    const link = w.find('.zc-page-link')
    expect(link.attributes('href')).toBe('https://example.com/docs?x=1')
    expect(link.attributes('rel')).toBe('noopener noreferrer')
    expect(link.text()).toBe('打开原页')
  })

  it('无标题的结果说「网页内容已读取」，不编一个标题', () => {
    const w = mountCard({ name: 'web_fetch', result: { ...okResult, title: '' } })
    expect(w.text()).toContain('网页内容已读取')
  })

  it('页面链接列表：有文字的用文字、没文字的回落 URL，且都带 rel', () => {
    const w = mountCard({ name: 'web_fetch', result: okResult })
    const links = w.find('[data-test="links"]').findAll('a')
    expect(links).toHaveLength(2)
    expect(links[0].text()).toBe('详情')
    expect(links[1].text()).toBe('https://example.com/b')
    expect(links[0].attributes('rel')).toBe('noopener noreferrer')
  })

  it('链接超过 8 条先折叠，展开后全量', async () => {
    const links = Array.from({ length: 10 }, (_, i) => ({
      text: `L${i}`, url: `https://e.com/${i}`,
    }))
    const w = mountCard({ name: 'web_fetch', result: { ...okResult, links } })
    expect(w.find('[data-test="links"]').findAll('a')).toHaveLength(8)
    const toggle = w.find('.zc-page-toggle')
    expect(toggle.text()).toContain('展开全部 10 条')
    await toggle.trigger('click')
    expect(w.find('[data-test="links"]').findAll('a')).toHaveLength(10)
  })

  it('失败是错误态：原因原文 + 不显示正文/链接', () => {
    const w = mountCard({ name: 'web_fetch',
      result: { ok: false, error: '目标站返回 403（拒绝访问）' } })
    expect(w.find('.zc-page').classes()).toContain('is-error')
    expect(w.find('[data-test="fetch-error"]').text()).toContain('403')
    expect(w.text()).not.toContain('页面链接')
  })

  it('二进制内容只回元信息：一句 note 说明「未读取正文」，卡片转警示色', () => {
    const w = mountCard({ name: 'web_fetch',
      result: { ok: true, url: 'https://example.com/a.pdf', status: 200,
                content_type: 'application/pdf',
                note: '内容不是文本（application/pdf），未读取正文',
                text: '', links: [], fetches_left: 9 } })
    expect(w.find('.zc-page').classes()).toContain('is-warn')
    expect(w.text()).toContain('未读取正文')
  })

  it('额度为 0 时明说「已用完」；-1（不限）不画成负次数', () => {
    const zero = mountCard({ name: 'web_fetch', result: { ...okResult, fetches_left: 0 } })
    expect(zero.text()).toContain('网页读取次数已用完')
    const unlimited = mountCard({ name: 'web_fetch', result: { ...okResult, fetches_left: -1 } })
    expect(unlimited.text()).not.toContain('还能抓')
  })

  it('进行中：地址取调用参数，画实时态而不是空卡', () => {
    const w = mountCard({ name: 'web_fetch', args: { url: 'https://example.com/' } },
      { line: '[web] 正在抓取 https://example.com/' })
    expect(w.text()).toContain('正在读取网页')
    expect(w.text()).toContain('[web] 正在抓取')
    expect(w.find('.zc-page-link').attributes('href')).toBe('https://example.com/')
  })

  it('遮断与截断必须说出口（外部内容进过凭据遮断闸）', () => {
    const w = mountCard({ name: 'web_fetch',
      result: { ...okResult, redacted: 2, truncated: true } })
    expect(w.text()).toContain('已遮断 2 处凭据')
    expect(w.text()).toContain('已按预算截断')
  })
})
