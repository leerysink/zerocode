// 文件类型的唯一判定处：树的图标配色、Monaco 语言、状态栏标签都从这张表取值。
// 只按文件名判，不读内容；大小写不敏感。扩展名表缺省归 file / plaintext，不返回空值。

const KIND_BY_EXT = {
  vue: 'vue',
  js: 'js', mjs: 'js', cjs: 'js', jsx: 'js',
  ts: 'ts', tsx: 'ts',
  json: 'json',
  md: 'md', markdown: 'md',
  html: 'html', htm: 'html',
  css: 'css', scss: 'css', less: 'css',
  py: 'py',
  java: 'java',
  sql: 'sql',
  yml: 'yaml', yaml: 'yaml',
  sh: 'shell', bash: 'shell',
  bat: 'bat', cmd: 'bat',
  xml: 'xml', svg: 'xml',
  ini: 'ini', conf: 'ini', cfg: 'ini', toml: 'ini',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', ico: 'image', bmp: 'image',
}

const LANG_BY_EXT = {
  vue: 'html',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  json: 'json',
  md: 'markdown', markdown: 'markdown',
  html: 'html', htm: 'html',
  css: 'css', scss: 'scss', less: 'less',
  py: 'python',
  java: 'java',
  sql: 'sql',
  yml: 'yaml', yaml: 'yaml',
  sh: 'shell', bash: 'shell',
  bat: 'bat', cmd: 'bat',
  xml: 'xml', svg: 'xml',
  ini: 'ini', conf: 'ini', cfg: 'ini', toml: 'ini',
}

const LANGUAGE_LABELS = {
  html: 'HTML',
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  json: 'JSON',
  markdown: 'Markdown',
  css: 'CSS',
  scss: 'SCSS',
  less: 'Less',
  python: 'Python',
  java: 'Java',
  sql: 'SQL',
  yaml: 'YAML',
  shell: 'Shell',
  bat: '批处理',
  dockerfile: 'Dockerfile',
  xml: 'XML',
  ini: 'INI',
  plaintext: '纯文本',
}

// 配色对齐 VSCode 文件图标的主流色系；未知类型给中性灰，不留空
const ICON_COLORS = {
  folder: '#d7ba7d',
  vue: '#42b883',
  js: '#f1e05a',
  ts: '#3178c6',
  json: '#cbcb41',
  md: '#519aba',
  html: '#e34c26',
  css: '#42a5f5',
  py: '#3572a5',
  java: '#b07219',
  sql: '#e38c00',
  yaml: '#cb171e',
  shell: '#89e051',
  bat: '#c1f12e',
  docker: '#0db7ed',
  xml: '#ff6600',
  image: '#a074c4',
  ini: '#6b7280',
  file: '#8c8c8c',
}

const FALLBACK_COLOR = '#8c8c8c'

function baseName(path) {
  const p = String(path ?? '').replace(/\\/g, '/')
  const i = p.lastIndexOf('/')
  return i === -1 ? p : p.slice(i + 1)
}

// 点文件（.env / .gitignore）的最后一段不是扩展名，不能当成 kind
function extOf(path) {
  const base = baseName(path).toLowerCase()
  if (base === 'dockerfile' || base.endsWith('.dockerfile')) return 'dockerfile'
  const i = base.lastIndexOf('.')
  if (i <= 0) return ''
  return base.slice(i + 1)
}

export function fileKind(path) {
  const base = baseName(path).toLowerCase()
  if (!base) return 'file'
  if (base === 'makefile' || base === 'license' || base.startsWith('.git')) return 'file'
  if (base.startsWith('.env')) return 'ini'
  const ext = extOf(path)
  if (ext === 'dockerfile') return 'docker'
  return KIND_BY_EXT[ext] || 'file'
}

export function languageOf(path) {
  const base = baseName(path).toLowerCase()
  if (base.startsWith('.env')) return 'ini'
  const ext = extOf(path)
  if (ext === 'dockerfile') return 'dockerfile'
  return LANG_BY_EXT[ext] || 'plaintext'
}

export function languageLabel(languageId) {
  return LANGUAGE_LABELS[languageId] || String(languageId ?? '')
}

export function isMarkdown(path) {
  const ext = extOf(path)
  return ext === 'md' || ext === 'markdown'
}

// 只有工作区根的 app.json 由平台托管；子目录里的同名文件是用户自己的
export function isReadOnlyPath(path) {
  const p = String(path ?? '').replace(/\\/g, '/').replace(/^\.\//, '')
  return p === 'app.json'
}

export function iconColor(kind) {
  return ICON_COLORS[kind] || FALLBACK_COLOR
}
