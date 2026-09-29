// SQL 语句拆分状态机（Ticket 004）。
//
// 002 的最小版按裸分号切，`'a;b'`、`-- ;`、`/* ; */`、`$$ ... ; $$` 里的**假分号**
// 会把一条语句错切成好几条。本票换成手写状态机（不依赖解析库）：只有站在
// "普通代码态"上的分号才算语句结束，其余时间整段跳过——
//   · 单引号串（`''` 是转义，不是闭合）· 双引号标识符（`""` 同理）
//   · `--` 行注释 · `/* */` 块注释（PG 允许**嵌套**，靠深度计数）
//   · `$$...$$` 与 `$tag$...$tag$` dollar-quote（`$1` 这种参数占位符不是标签起头）
// 未闭合的引号/注释**不抛异常也不瞎猜拆分点**：把当前剩余段整段作为一条返回，
// 让后端的原文错误去说话——用户手打到一半时按钮不该失灵。
// 纯空白段与纯注释段直接丢弃（`-- hi` 单独一段不产出可执行项）；
// 返回 [{sql, startLine}]，startLine 是 1 基起始行，引号与注释内部的换行同样计入。
const SPACE = /\s/
// dollar-quote 起始标签：$$ 或 $tag$。标签首字符必须是字母/下划线，故 $1 不会误判。
const DOLLAR_TAG = /\$(?:\$|[A-Za-z_][A-Za-z0-9_]*\$)/y
const UNCLOSED = -1

/**
 * 把一段 SQL 文本切成可独立执行的语句，只在真分号处切。
 * @param {string} text 编辑器里的原文
 * @returns {{sql: string, startLine: number}[]} startLine 为 1 基的**起始行**（跨行语句只记第一行）
 */
export function splitSqlStatements(text) {
  const out = []
  if (!text) return out
  const n = text.length
  let segStart = 0
  let segLine = 1 // segStart 所在行
  let line = 1 // 当前扫描位置所在行
  let i = 0
  while (i < n) {
    const ch = text[i]
    if (ch === ';') {
      pushSegment(out, text.slice(segStart, i), segLine)
      i += 1
      segStart = i
      segLine = line
      continue
    }
    if (ch === '\n') {
      line += 1
      i += 1
      continue
    }
    const end = skipRegion(text, i)
    if (end === UNCLOSED) {
      // 未闭合：剩下的一整段原样交出去，前面已闭合的语句照常保留。
      pushSegment(out, text.slice(segStart, n), segLine)
      return out
    }
    if (end > i) {
      line += countNewlines(text.slice(i, end))
      i = end
      continue
    }
    i += 1
  }
  pushSegment(out, text.slice(segStart, n), segLine)
  return out
}

/**
 * i 处若是引号/注释/dollar-quote 的起点，返回其**终点之后**的下标。
 * @returns {number} i 表示这里不是安全区起点；UNCLOSED 表示一路到文末都没闭合。
 */
function skipRegion(text, i) {
  const ch = text[i]
  if (ch === "'" || ch === '"') return skipQuoted(text, i, ch)
  if (ch === '-' && text[i + 1] === '-') {
    const nl = text.indexOf('\n', i)
    return nl === -1 ? text.length : nl // 换行交回主循环计数，行号才不会漏
  }
  if (ch === '/' && text[i + 1] === '*') return skipBlockComment(text, i)
  if (ch === '$') return skipDollarQuoted(text, i)
  return i
}

function skipQuoted(text, i, quote) {
  let j = i + 1
  while (j < text.length) {
    if (text[j] === quote) {
      if (text[j + 1] === quote) {
        j += 2 // '' / "" 是转义，字符串还没完
        continue
      }
      return j + 1
    }
    j += 1
  }
  return UNCLOSED
}

// PG 的块注释可嵌套：内层 */ 只消一层，不能把它当外层结束，否则后面的假分号又会漏出来。
function skipBlockComment(text, i) {
  let depth = 1
  let j = i + 2
  while (j < text.length) {
    if (text[j] === '/' && text[j + 1] === '*') {
      depth += 1
      j += 2
      continue
    }
    if (text[j] === '*' && text[j + 1] === '/') {
      depth -= 1
      j += 2
      if (depth === 0) return j
      continue
    }
    j += 1
  }
  return UNCLOSED
}

function skipDollarQuoted(text, i) {
  DOLLAR_TAG.lastIndex = i
  const m = DOLLAR_TAG.exec(text)
  if (!m) return i // $1 / 孤立的 $：不是标签起头，按普通字符走
  const tag = m[0]
  const close = text.indexOf(tag, i + tag.length)
  return close === -1 ? UNCLOSED : close + tag.length
}

// 去掉首尾空白后入栈；空段与纯注释段直接丢弃（尾分号不产生空语句）。
function pushSegment(out, raw, lineBase) {
  let from = 0
  while (from < raw.length && SPACE.test(raw[from])) from++
  let to = raw.length
  while (to > from && SPACE.test(raw[to - 1])) to--
  if (to <= from) return
  const sql = raw.slice(from, to)
  if (!hasExecutableCode(sql)) return
  out.push({ sql, startLine: lineBase + countNewlines(raw.slice(0, from)) })
}

// 段里除了注释和空白还剩别的吗？注释起头由 skipRegion 判定并整段跳过，
// 其余非空白字符（含字面量）一律算代码。注释没闭合时返回 true：
// 原文照送后端报错，前端不把它当"纯注释"悄悄丢掉。
function hasExecutableCode(text) {
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (SPACE.test(ch)) continue
    const isComment = (ch === '-' && text[i + 1] === '-') || (ch === '/' && text[i + 1] === '*')
    if (!isComment) return true
    const end = skipRegion(text, i)
    if (end === UNCLOSED) return true
    i = end - 1 // for 再 +1 正好落在终点
  }
  return false
}

function countNewlines(s) {
  let n = 0
  for (let i = 0; i < s.length; i++) if (s[i] === '\n') n++
  return n
}
