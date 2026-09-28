// SQL 语句拆分（Ticket 002 最小版）。
//
// 本票只做"裸分号切段"：按 `;` 切、trim 后丢弃空/纯空白段、带 1 基的起始行号。
// 已知限制（Ticket 004 用状态机解决）：字符串字面量 `'a;b'`、`$$ ... ; ... $$`
// dollar-quote、行注释 `-- ;` / 块注释 `/* ; */` 里的**假分号**同样会被切开。
// 拆出来的每条原样送后端批量端点，服务端不再二次拆分。
const SPACE = /\s/

/**
 * 把一段 SQL 文本按分号切成可独立执行的语句。
 * @param {string} text 编辑器里的原文
 * @returns {{sql: string, startLine: number}[]} startLine 为 1 基的**起始行**（跨行语句只记第一行）
 */
export function splitSqlStatements(text) {
  const out = []
  if (!text) return out
  let segStart = 0
  let lineBase = 1 // 本段起点所在的那一行（尚未减去段内前导空白）
  for (let i = 0; i <= text.length; i++) {
    if (i < text.length && text[i] !== ';') continue
    const raw = text.slice(segStart, i) // i === text.length 时收尾最后一段（无分号也算一条）
    pushSegment(out, raw, lineBase)
    lineBase += countNewlines(raw)
    segStart = i + 1
  }
  return out
}

// 去掉首尾空白后入栈；纯空白/空段直接丢弃（尾分号不产生空语句）。
function pushSegment(out, raw, lineBase) {
  let from = 0
  while (from < raw.length && SPACE.test(raw[from])) from++
  let to = raw.length
  while (to > from && SPACE.test(raw[to - 1])) to--
  if (to <= from) return
  out.push({ sql: raw.slice(from, to), startLine: lineBase + countNewlines(raw.slice(0, from)) })
}

function countNewlines(s) {
  let n = 0
  for (let i = 0; i < s.length; i++) if (s[i] === '\n') n++
  return n
}
