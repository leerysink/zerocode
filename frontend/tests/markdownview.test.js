import { describe, it, expect, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import MarkdownView from '../src/components/MarkdownView.vue'

// markdown-it 构造计数：vi.hoisted 保证 mock 工厂可引用共享计数器（改对象属性，避免 const 绑定不可再赋值）
const counter = vi.hoisted(() => ({ mdCalls: 0 }))
vi.mock('markdown-it', async (importOriginal) => {
  const actual = await importOriginal()
  class Wrapped extends actual.default {
    constructor(...args) {
      super(...args)
      counter.mdCalls++
    }
  }
  return { default: Wrapped }
})

// 无 @vue/test-utils：用 vue/server-renderer 做 SSR 渲染断言（node 环境即可，不引新依赖）
const renderComp = (comp, props) =>
  renderToString(createSSRApp({ render: () => h(comp, props) }))

describe('MarkdownView', () => {
  it('渲染 Markdown（标题/行内代码）', async () => {
    const html = await renderComp(MarkdownView, { content: '# 标题\n\n行内 `code` 文本' })
    expect(html).toContain('<h1>')
    expect(html).toContain('标题')
    expect(html).toContain('<code>')
  })

  it('html:false——原始 HTML 被转义不渲染', async () => {
    const html = await renderComp(MarkdownView, { content: '<img src=x onerror="alert(1)">' })
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
  })

  it('markdown-it 配置模块级单例：多次实例化组件只构造一次', async () => {
    await renderComp(MarkdownView, { content: 'a' })
    await renderComp(MarkdownView, { content: 'b' })
    // 若配置被误移入 <script setup>（每次实例化都 new），此处会 ≥3
    expect(counter.mdCalls).toBe(1)
  })
})
