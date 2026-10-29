// 工作台 Markdown 渲染视图的纯逻辑：独立 markdown-it 实例 + 站内相对路径改写。
// 与聊天用的 MarkdownView 刻意分开：那边 breaks:true（聊天语境），这边是文档语义，单换行不折成 <br>。
// 链接白名单在这里收口：外链新标签、站内相对路径开页签、javascript:/data:/越界路径降级为普通文本。
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import { downloadUrl } from './fsNav'
import { workspaceFilesPath } from './openFolder'

const SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/
const SAFE_LINK_SCHEME = /^(?:https?|mailto|tel):/i
const SAFE_IMAGE_SCHEME = /^https?:/i

// 把 Markdown 里的相对地址按「当前文件所在目录」归一化；不是工作区内的相对路径一律 null。
// query/hash 只保留路径部分；越根（多余的 ..）返回 null，绝不让它跳到工作区外。
export function resolveWorkspaceRel(filePath, href) {
  const raw = String(href ?? '').trim()
  if (!raw) return null
  if (raw.startsWith('#')) return null
  if (SCHEME_RE.test(raw)) return null
  if (raw.startsWith('/')) return null
  let decoded
  try {
    decoded = decodeURIComponent(raw.split(/[?#]/, 1)[0])
  } catch {
    return null
  }
  if (!decoded) return null
  const parts = String(filePath ?? '').split('/').slice(0, -1).filter(Boolean)
  for (const seg of decoded.split('/')) {
    if (!seg || seg === '.') continue
    if (seg === '..') {
      if (!parts.length) return null
      parts.pop()
      continue
    }
    parts.push(seg)
  }
  return parts.length ? parts.join('/') : null
}

function planLink(href, env) {
  const h = String(href ?? '').trim()
  if (!h) return { kind: 'text' }
  if (h.startsWith('#')) return { kind: 'anchor' }
  if (h.startsWith('//')) return { kind: 'external' }
  if (h.startsWith('/')) return { kind: 'text' }
  if (SCHEME_RE.test(h)) return { kind: SAFE_LINK_SCHEME.test(h) ? 'external' : 'text' }
  const rel = resolveWorkspaceRel(env.filePath, h)
  const href2 = rel ? workspaceFilesPath(env.workspaceId, rel) : ''
  if (!rel || !href2) return { kind: 'text' }
  return { kind: 'file', rel, href: href2 }
}

function planImage(src, env) {
  const s = String(src ?? '').trim()
  if (!s) return { kind: 'keep' }
  if (s.startsWith('//') || s.startsWith('/')) return { kind: 'keep' }
  if (SCHEME_RE.test(s)) return SAFE_IMAGE_SCHEME.test(s) ? { kind: 'keep' } : { kind: 'text' }
  const rel = resolveWorkspaceRel(env.filePath, s)
  if (!rel) return { kind: 'keep' }
  return { kind: 'file', href: downloadUrl(env.workspaceId, rel, true) }
}

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: false,
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return `<pre class="hljs"><code>${hljs.highlight(code, { language: lang, ignoreIllegals: true }).value}</code></pre>`
      } catch {
        /* 未知语法退回转义输出 */
      }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(code)}</code></pre>`
  },
})

// markdown-it 默认拒绝 javascript: 链接时会把整段语法当字面文本渲染（协议串也跟着露出来）。
// 这里先放行解析，再在渲染规则里按白名单降级——正文只留链接文字，不留可点协议。
md.validateLink = () => true

function markLinks(state) {
  const env = state.env || {}
  for (const token of state.tokens) {
    if (token.type !== 'inline' || !Array.isArray(token.children)) continue
    const stack = []
    for (const child of token.children) {
      if (child.type === 'link_open') {
        child.meta = { ...child.meta, zcLink: planLink(child.attrGet('href'), env) }
        stack.push(child)
      } else if (child.type === 'link_close') {
        const open = stack.pop()
        child.meta = { ...child.meta, zcSuppress: !open || open.meta?.zcLink?.kind === 'text' }
      } else if (child.type === 'image') {
        child.meta = { ...child.meta, zcImage: planImage(child.attrGet('src'), env) }
      }
    }
  }
}

md.core.ruler.push('zc_workbench_links', markLinks)

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const plan = token.meta?.zcLink
  if (!plan || plan.kind === 'text') return ''
  if (plan.kind === 'external') {
    token.attrSet('target', '_blank')
    token.attrSet('rel', 'noopener noreferrer')
  } else if (plan.kind === 'file') {
    token.attrSet('href', plan.href)
    token.attrSet('data-file-path', plan.rel)
  }
  return self.renderToken(tokens, idx, options)
}

md.renderer.rules.link_close = (tokens, idx, options, env, self) => (
  tokens[idx].meta?.zcSuppress ? '' : self.renderToken(tokens, idx, options)
)

const defaultImageRule = md.renderer.rules.image

md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const plan = token.meta?.zcImage
  if (plan?.kind === 'text') return md.utils.escapeHtml(token.content || '')
  if (plan?.kind === 'file') token.attrSet('src', plan.href)
  return defaultImageRule(tokens, idx, options, env, self)
}

export function renderDocMarkdown(content, options = {}) {
  return md.render(String(content ?? ''), {
    filePath: String(options.filePath ?? ''),
    workspaceId: options.workspaceId,
  })
}
