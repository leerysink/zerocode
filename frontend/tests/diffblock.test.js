import { describe, it, expect } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import DiffBlock from '../src/components/DiffBlock.vue'

// 无 @vue/test-utils：用 vue/server-renderer 做 SSR 渲染断言（node 环境即可，不引新依赖）
const renderComp = (comp, props) =>
  renderToString(createSSRApp({ render: () => h(comp, props) }))

describe('DiffBlock 头行过滤', () => {
  it('只过滤前两行 `--- `/`+++ ` 头行；正文中的 `--- x`/`+++ y` 内容行保留', async () => {
    // 正文 del 行原文内容是 `-- x`（raw 拼上删除标记为 `--- x`）、add 行原文内容是 `++ y`
    // （raw 为 `+++ y`）——旧实现按前缀全量过滤会误删这两行
    const diff = ['--- a/x', '+++ b/x', '@@ -1,2 +1,2 @@', '--- x', '+++ y', '+tail'].join('\n')
    const html = await renderComp(DiffBlock, { diff, path: 'x', action: 'edit' })
    expect(html).not.toContain('--- a/x')
    expect(html).not.toContain('+++ b/x')
    expect(html).toContain('--- x')
    expect(html).toContain('+++ y')
    expect(html).toContain('+tail')
  })
})
