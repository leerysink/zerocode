// SQL 格式化纯函数（Ticket 006 / US-02）。
//
// 只负责"给一段文本和选区边界，还一份新文本和新选区"，不碰 CodeMirror：
// happy-dom 起不来真实编辑器，格式化必须能脱离编辑器被单测覆盖，SqlEditor 只当读数/写回通道。
//
// 两条口径是实测 sql-formatter 15.9 之后定下的（工单 006 裁定）：
// - **只有它抛语法错**（未闭合引号、未闭合括号这类 token 级错误）才回退原文；
//   `select from` 这种"残缺但可解析"的片段它照排不误，我们也就用它的结果——想严格"返回原文"
//   得自己判"像不像完整语句"，判据不可靠还会把合法片段拦下来。
// - 它会把整段 **trim** 掉。用户拿空行分隔语句，直接端上去会被吃掉，所以先剥出选区内的
//   首尾空白，只格式化中间核心，排完再把空白贴回去。
import { format } from 'sql-formatter'

export const FORMAT_OPTIONS = { language: 'postgresql', keywordCase: 'upper' }

const SPACE = /\s/

/**
 * 按选区格式化：无选区（下标缺失、非整数、或起止相等）格式化全段；
 * 有选区只重排选中片段并原地替换。
 * @param {string} text 编辑器全文
 * @param {number|null} selStart 选区起点（CodeMirror 字符偏移）
 * @param {number|null} selEnd 选区终点
 * @returns {{text: string, inserted: string, from: number, to: number,
 *   selStart: number|null, selEnd: number|null, ok: boolean, kind: 'ok'|'empty'|'error'}}
 *   inserted 是写回 [from, to] 的那段新内容；from/to 是**钳制交换后**的实际替换区间，
 *   调用方直接拿它写回，不必自己判"无选区就是整段"；selStart/selEnd 框住重排后的核心
 *   （无选区时为 null，表示调用方不该重设选区）；empty/error 下 text 原样、
 *   inserted 为空串且区间收成 0-0（万一被误用去写回也不会吃掉一个字符）。
 */
export function formatRange(text, selStart, selEnd) {
  const doc = typeof text === 'string' ? text : ''
  const range = resolveRange(doc, selStart, selEnd)
  const raw = doc.slice(range.from, range.to)
  const coreStart = countLeadWs(raw)
  const coreEnd = raw.length - countTrailWs(raw)
  const core = raw.slice(coreStart, coreEnd)
  if (!core) return unchanged(doc, false, 'empty')

  let formatted
  try {
    formatted = format(core, FORMAT_OPTIONS)
  } catch {
    // 语法怪异的片段：原文一个字不动，让调用方去轻提示（用户可能正打在中间）
    return unchanged(doc, false, 'error')
  }

  const inserted = raw.slice(0, coreStart) + formatted + raw.slice(coreEnd)
  return {
    text: doc.slice(0, range.from) + inserted + doc.slice(range.to),
    inserted,
    from: range.from,
    to: range.to,
    selStart: range.hasSel ? range.from + coreStart : null,
    selEnd: range.hasSel ? range.from + coreStart + formatted.length : null,
    ok: true,
    kind: 'ok',
  }
}

function unchanged(doc, ok, kind) {
  // from/to 收成 0-0：配合 inserted=''，把"没成功"写成一次真正的空替换
  return { text: doc, inserted: '', from: 0, to: 0, selStart: null, selEnd: null, ok, kind }
}

// 交换 + 钳制后若塌成一个点（越界到文末、或本来就是光标），按"无选区"处理走全段：
// 与其格式化一个空片段，不如把整段排一遍——这与用户在文档末尾点「格式化」的预期一致。
function resolveRange(doc, a, b) {
  if (!Number.isInteger(a) || !Number.isInteger(b) || a === b) {
    return { from: 0, to: doc.length, hasSel: false }
  }
  const from = Math.max(0, Math.min(a, b))
  const to = Math.min(doc.length, Math.max(a, b))
  if (from >= to) return { from: 0, to: doc.length, hasSel: false }
  return { from, to, hasSel: true }
}

function countLeadWs(s) {
  let i = 0
  while (i < s.length && SPACE.test(s[i])) i++
  return i
}

function countTrailWs(s) {
  let i = 0
  while (i < s.length && SPACE.test(s[s.length - 1 - i])) i++
  return i
}
