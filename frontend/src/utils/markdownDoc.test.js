import { describe, it, expect } from 'vitest'
import { renderDocMarkdown, resolveWorkspaceRel } from './markdownDoc'

const render = (md, filePath = 'docs/readme.md', workspaceId = 7) =>
  renderDocMarkdown(md, { filePath, workspaceId })

describe('resolveWorkspaceRel 相对路径归一化', () => {
  it('相对当前文件所在目录解析 ./ 与 ../', () => {
    expect(resolveWorkspaceRel('docs/readme.md', './img/a.png')).toBe('docs/img/a.png')
    expect(resolveWorkspaceRel('docs/readme.md', '../x.png')).toBe('x.png')
    expect(resolveWorkspaceRel('a/b/c.md', '../../d.md')).toBe('d.md')
  })

  it('带 query/hash 的链接只取路径部分', () => {
    expect(resolveWorkspaceRel('docs/a.md', './b.md?x=1#sec')).toBe('docs/b.md')
    expect(resolveWorkspaceRel('docs/a.md', './b%20c.md')).toBe('docs/b c.md')
  })

  it('锚点、站内绝对、外链协议、越界都返回 null', () => {
    expect(resolveWorkspaceRel('a.md', '#top')).toBeNull()
    expect(resolveWorkspaceRel('a.md', '/api/x')).toBeNull()
    expect(resolveWorkspaceRel('a.md', '//cdn/x.png')).toBeNull()
    expect(resolveWorkspaceRel('a.md', 'https://e.com/x')).toBeNull()
    expect(resolveWorkspaceRel('a.md', 'mailto:a@b.c')).toBeNull()
    expect(resolveWorkspaceRel('a.md', 'javascript:alert(1)')).toBeNull()
    expect(resolveWorkspaceRel('a.md', '../escape.md')).toBeNull()
    expect(resolveWorkspaceRel('a.md', '')).toBeNull()
  })
})

describe('renderDocMarkdown 相对资源', () => {
  it('相对图片改写为 inline 下载地址', () => {
    const html = render('![图](./img/a.png)')
    expect(html).toContain('/api/fs/download?workspace_id=7&amp;path=docs%2Fimg%2Fa.png&amp;inline=1')
    expect(html).toContain('alt="图"')
  })

  it('越根图片不改写，保持裂图', () => {
    const html = render('![x](../x.png)', 'a.md')
    expect(html).not.toContain('/api/fs/download')
    expect(html).toContain('src="../x.png"')
  })

  it('相对文件链接带 data-file-path 与可复制的站内地址', () => {
    const html = render('[说明](./guide.md?x=1#top)')
    expect(html).toContain('data-file-path="docs/guide.md"')
    expect(html).toContain('/workspaces/7/files?file=docs%2Fguide.md')
  })

  it('外链新标签并带 noopener', () => {
    const html = render('[官网](https://example.com/a)')
    expect(html).toContain('href="https://example.com/a"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('javascript: 与越界链接降级为普通文本', () => {
    const js = render('[坏](javascript:alert(1))')
    expect(js).not.toContain('<a ')
    expect(js).not.toContain('javascript:')
    const escape = render('[逃](../x.md)', 'a.md')
    expect(escape).not.toContain('<a ')
    expect(escape).toContain('逃')
  })

  it('纯锚点保持原链接，不当站内文件', () => {
    const html = render('[跳](#section)')
    expect(html).toContain('href="#section"')
    expect(html).not.toContain('data-file-path')
  })
})

describe('renderDocMarkdown 内容渲染', () => {
  it('原始 HTML 被转义', () => {
    const html = render('<script>alert(1)</script>')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })

  it('表格正常渲染', () => {
    const html = render('| a | b |\n|---|---|\n| 1 | 2 |')
    expect(html).toContain('<table>')
    expect(html).toContain('<td>1</td>')
  })

  it('代码块有 hljs 高亮并按语言解析', () => {
    const html = render('```js\nconst a = 1\n```')
    expect(html).toContain('hljs')
    expect(html).toContain('hljs-keyword')
  })

  it('单换行不强行变 <br>（文档语义，与聊天气泡不同）', () => {
    const html = render('第一行\n第二行')
    expect(html).not.toContain('<br>')
  })
})
