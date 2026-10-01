// Ticket 008A / US-15：SQL 表名与列名补全的纯逻辑层。
//
// 为什么不写在 SqlEditor.vue 里：CodeMirror 在 happy-dom 起不来（ADR 0006），
// 而"光标在这儿该补什么"、"表名从哪一段抠出来"、"缓存什么时候失效"这些判定
// 恰恰是最需要被测的部分。所以这里只做纯函数 + 一个不带 UI 的上下文对象，
// SqlEditor.vue 只把 completions(doc, caret) 接到 autocompletion 上。
//
// 数据源沿用既有的 dbTables / dbTableSchema，本票不新增后端接口（spec §2）。
// 补全是锦上添花，任何取数失败都只降级成"没有候选"，绝不抛给编辑器。

export const MAX_COMPLETIONS = 200

// 表名触发词：只有光标前最近的词是这些之一，才认为在填表名。
// 少了这张表，`SELECT wid` 也会被表名候选糊住用户正要打的列名。
const TABLE_TRIGGERS = new Set(['from', 'join', 'into', 'update', 'table', 'truncate', 'exists'])

const RE_IDENT_TAIL = /[\p{L}\p{N}_$]*$/u
// 表名.前缀 —— 前缀允许是空、裸标识符，或未闭合的双引号串
const RE_COLUMN_TAIL = /((?:[\p{L}\p{N}_$]+|"[^"\n]*"))\s*\.\s*([\p{L}\p{N}_$]*|"[^"\n]*)$/u
// 光标前最近的完整词（含引号词），用来判触发词
const RE_LAST_WORD = /((?:[\p{L}\p{N}_$]+|"[^"\n]*"))\s*$/u

const isIdentChar = (ch) => !!ch && /[\p{L}\p{N}_$]/u.test(ch)

// 后端返回的是 [{ name, type }]，但也见过裸字符串数组和一堆脏值，一律收成 string[]。
export function normalizeTables(tables) {
  if (!Array.isArray(tables)) return []
  const out = []
  for (const item of tables) {
    const raw = typeof item === 'string'
      ? item
      : (item && typeof item === 'object' && typeof item.name === 'string' ? item.name : '')
    const name = raw.trim()
    if (name && !out.includes(name)) out.push(name)
  }
  return out
}

// PG 折叠规则：未加引号的标识符进目录时就被折成小写，加引号的原样保留。
// 补全要拿这个名字去查 schema，折叠错了就永远查不到。
function normalizeIdentifier(raw) {
  if (typeof raw !== 'string' || !raw) return ''
  if (raw.startsWith('"') && raw.endsWith('"') && raw.length >= 2) return raw.slice(1, -1)
  return raw.toLowerCase()
}

// 只有"小写、字母或下划线开头、不含奇奇怪怪"的名字能裸写，其余都得加引号。
function quoteIdentifier(name) {
  return /^[a-z_][a-z0-9_]*$/.test(name) ? name : `"${String(name).replace(/"/g, '""')}"`
}

function clampCaret(doc, caret) {
  if (typeof doc !== 'string' || typeof caret !== 'number' || !Number.isFinite(caret)) return null
  return Math.max(0, Math.min(Math.floor(caret), doc.length))
}

// 表名位置：光标前最近的词是触发词，才给出 { kind:'table', prefix, from, quoted }。
function resolveTableTarget(before) {
  const run = RE_IDENT_TAIL.exec(before)[0]
  const cut = before.length - run.length
  // cut-1 处是引号且它左边不是标识符 —— 这是开引号，from 从开引号起算，
  // 这样替换时能把整个（可能已闭合的）引号词一起换掉，不会写出 "" 这种残骸。
  const quoted = before[cut - 1] === '"' && !isIdentChar(before[cut - 2])
  const from = quoted ? cut - 1 : cut
  const word = RE_LAST_WORD.exec(before.slice(0, from))
  if (!word || !TABLE_TRIGGERS.has(word[1].toLowerCase())) return null
  return { kind: 'table', prefix: run, from, quoted }
}

// 列名位置：光标前是 `名字.`（可再带一段前缀），取最后一段表名去查列。
function resolveColumnTarget(before, caret) {
  const m = RE_COLUMN_TAIL.exec(before)
  if (!m) return null
  const tail = m[2] || ''
  const quoted = tail.startsWith('"')
  return {
    kind: 'column',
    table: normalizeIdentifier(m[1]),
    prefix: quoted ? tail.slice(1) : tail,
    from: caret - tail.length,
    quoted,
  }
}

// 判定该补表名、补列名，还是什么都不补。
// 返回 null 表示"这里不该弹补全"；只看光标之前的文本，后面的内容一概不管。
export function resolveCompletionTarget(doc, caret) {
  const at = clampCaret(doc, caret)
  if (at === null || at <= 0) return null
  const before = doc.slice(0, at)
  return resolveColumnTarget(before, at) || resolveTableTarget(before) || null
}

// 补全上下文：持有 env / appId / tables 和一张 {env}:{table} → 列名 的缓存。
// 由 SqlConsole 构造并保持引用稳定，切 env 用 setEnv 原地改，好让 SqlEditor
// 不必因为环境变化就重建整条 CodeMirror 扩展链。
export function createCompletionContext(options = {}) {
  let appId = options.appId ?? null
  let env = options.env ?? null
  let tables = normalizeTables(options.tables)
  const fetchSchema = typeof options.fetchSchema === 'function' ? options.fetchSchema : null
  const cache = new Map()
  const pending = new Map()

  function clearCache() {
    cache.clear()
    pending.clear()
  }

  async function columnsOf(table) {
    const name = normalizeIdentifier(table)
    if (!name || !fetchSchema) return []
    const key = `${env}:${name}`
    if (cache.has(key)) return cache.get(key)
    if (pending.has(key)) return pending.get(key)
    const inflight = Promise.resolve()
      .then(() => fetchSchema(appId, env, name))
      .then((res) => {
        const columns = normalizeTables((res && res.data && res.data.columns) ?? (res && res.columns))
        cache.set(key, columns)
        return columns
      })
      .catch(() => []) // 表不存在、404、500 都是"这一轮没有列候选"，不缓存、下次重试
      .finally(() => pending.delete(key))
    pending.set(key, inflight)
    return inflight
  }

  function toOptions(names, prefix, type) {
    const needle = String(prefix ?? '').toLowerCase()
    const hit = needle
      ? names.filter((name) => name.toLowerCase().startsWith(needle))
      : names
    return hit.slice(0, MAX_COMPLETIONS).map((label) => ({ label, type, apply: quoteIdentifier(label) }))
  }

  async function completions(doc, caret = (doc == null ? 0 : String(doc).length)) {
    const at = clampCaret(doc, caret)
    if (at === null) return { kind: null, from: 0, options: [] }
    const target = resolveCompletionTarget(doc, at)
    if (!target) return { kind: null, from: at, options: [] }
    if (target.kind === 'table') {
      return { ...target, options: toOptions(tables, target.prefix, 'table') }
    }
    const columns = await columnsOf(target.table)
    return { ...target, options: toOptions(columns, target.prefix, 'column') }
  }

  return {
    cache,
    columnsOf,
    completions,
    get appId() { return appId },
    get env() { return env },
    get tables() { return tables },
    setAppId(next) {
      if (next === appId) return
      appId = next
      clearCache()
    },
    setEnv(next) {
      if (next === env) return
      env = next
      clearCache()
    },
    setTables(next) {
      tables = normalizeTables(next)
    },
  }
}
