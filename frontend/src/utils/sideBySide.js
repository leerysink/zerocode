// 双栏对齐模型：unified diff 决定「哪几行改了什么、怎么配对」，变更前后全文提供行号与折叠段的原文。
// 为什么要带全文进来：difflib.unified_diff 默认只给 3 行上下文，未变更的段落根本不在 diff 里，
// 而详情页既要标真实行号、又要让用户点开「展开 N 行未变更」看到原文——两头都得数全文。
// 输入一律是 app/agent/tools.py:_record_change 落库的那三列（先遮凭据、再据遮后文本算 diff，
// 所以三列互相对应，这里可以放心地拿 diff 当对齐依据、拿全文当行号尺子）。

// Python 的 splitlines(keepends=True) 与 JS 的 split('\n') 差在最后那个空元素上：
// 'a\nb\n' 在 Python 是 2 行，split 出来却有 3 段。行号尺子必须跟 difflib 数的一致。
function linesOf(text) {
  const arr = String(text ?? '').split('\n')
  if (arr.length && arr[arr.length - 1] === '') arr.pop()
  return arr
}

function parseHunkHeader(line) {
  // `@@ -l,s +l,s @@`：difflib 省掉 `,1` 时按 1 行算
  const m = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line)
  return {
    leftStart: Number(m[1]),
    leftCount: m[2] === undefined ? 1 : Number(m[2]),
    rightStart: Number(m[3]),
    rightCount: m[4] === undefined ? 1 : Number(m[4]),
  }
}

export function buildSideBySide({ diff, before, after }) {
  const beforeLines = linesOf(before)
  const afterLines = linesOf(after)
  const lines = String(diff ?? '').split('\n')
  if (lines.length && lines[lines.length - 1] === '') lines.pop()

  const rows = []
  let left = 0 // 已消费的旧文件行数，同时是下一行的行号基准
  let right = 0
  let i = 0
  // 首个 @@ 之前的行全部丢掉：difflib 固定在最前面发 `--- a/x` / `+++ b/x` 两行文件头，
  // 而 hunk 正文里的行必带 ' '/'-'/'+' 前缀，不可能以 @@ 开头。所以「首个 @@ 前」是精确的文件头位置，
  // 不像按行号 <2 猜，会把正文里形如 `--- x`（原文 `-- x` 的删除行）误当文件头滤掉。
  while (i < lines.length && !lines[i].startsWith('@@')) i += 1

  // difflib 对「前后完全相同」的输入一行都不吐（连文件头都没有），所以没有 @@ 就是没有变更。
  // 这条必须判在尾部折叠之前，否则整篇文件会被折成一条「N 行未变更」，看着像发生过什么。
  if (i >= lines.length) return IDENTICAL

  while (i < lines.length) {
    const hunk = parseHunkHeader(lines[i])
    i += 1
    // hunk 之前的未变更段落：上一 hunk 末尾与本 hunk 起点之间的那些行，两侧行数必然相等
    if (hunk.leftStart > left + 1) {
      rows.push({
        kind: 'gutter',
        count: hunk.leftStart - left - 1,
        leftStart: left + 1,
        rightStart: right + 1,
      })
    }
    // `-0,0`（新建）与 `+0,0`（删除）时行号起点是 0，基准不能落成 -1
    left = Math.max(hunk.leftStart - 1, 0)
    right = Math.max(hunk.rightStart - 1, 0)

    let seenLeft = 0
    let seenRight = 0
    let dels = []
    let adds = []
    const flush = () => {
      // difflib 把 replace 组先吐完 '-' 行再吐 '+' 行；两侧按序同行对齐（VS Code 并排比较的排法），
      // 长短不一时短的一侧补 gap。行号按各自这一跑的序号递增，不能整跑共用一个号。
      const n = Math.max(dels.length, adds.length)
      for (let k = 0; k < n; k += 1) {
        rows.push({
          kind: 'row',
          left: k < dels.length
            ? { n: left + k + 1, text: dels[k], cls: 'del' }
            : { n: null, text: '', cls: 'gap' },
          right: k < adds.length
            ? { n: right + k + 1, text: adds[k], cls: 'add' }
            : { n: null, text: '', cls: 'gap' },
        })
      }
      left += dels.length
      right += adds.length
      dels = []
      adds = []
    }

    // 两侧计数是「或」而不是「与」：新建的 hunk 是 -0,0（旧侧一行没有），
    // 按「与」判会一步都不走，正文里的 `+xxx` 永远读不到
    while (i < lines.length && (seenLeft < hunk.leftCount || seenRight < hunk.rightCount)) {
      const line = lines[i]
      if (line.startsWith('@@')) break
      i += 1
      if (line.startsWith('+')) {
        adds.push(line.slice(1))
        seenRight += 1
      } else if (line.startsWith('-')) {
        dels.push(line.slice(1))
        seenLeft += 1
      } else {
        flush()
        seenLeft += 1
        seenRight += 1
        rows.push({
          kind: 'row',
          left: { n: left + 1, text: line.slice(1), cls: 'ctx' },
          right: { n: right + 1, text: line.slice(1), cls: 'ctx' },
        })
        left += 1
        right += 1
      }
    }
    flush()
  }

  // 最后一个 hunk 之后的未变更尾部（diff 只带上下文，尾巴全在全文里）
  if (beforeLines.length > left) {
    rows.push({
      kind: 'gutter',
      count: beforeLines.length - left,
      leftStart: left + 1,
      rightStart: right + 1,
    })
  }

  // 回验：解析出来的每一行，文本必须与全文同一行号上的那一行完全一致。
  // 对不上就说明 diff 文本没能还原出真相（无行尾换行时 difflib 会把新旧两行挤成 `-b+B`），
  // 此时行号与配色全是假的——如实报 unaligned，由页面退回全文双栏，不铺一套骗人的对齐。
  for (const r of rows) {
    if (r.kind !== 'row') continue
    if (r.left.cls !== 'gap' && beforeLines[r.left.n - 1] !== r.left.text) return UNALIGNED
    if (r.right.cls !== 'gap' && afterLines[r.right.n - 1] !== r.right.text) return UNALIGNED
  }

  return {
    status: 'ok',
    rows,
    added: rows.filter((r) => r.kind === 'row' && r.right.cls === 'add').length,
    removed: rows.filter((r) => r.kind === 'row' && r.left.cls === 'del').length,
  }
}

const UNALIGNED = Object.freeze({ status: 'unaligned', rows: [], added: 0, removed: 0 })
const IDENTICAL = Object.freeze({ status: 'identical', rows: [], added: 0, removed: 0 })

// 展开一条折叠段。未变更段两侧的行数与起点是 hunk 头钉死的，所以原文直接取两侧全文，
// 不需要（也不该）在这儿重算一遍 diff——重算就得引一个 diff 库，而 diff 早就由后端算好了。
// limit 存在的理由只有一个：一万行未变更一次铺出来会把标签页冻住，所以留「继续展开」。
export function expandGutter(gutter, { before, after }, limit = 500) {
  const beforeLines = linesOf(before)
  const afterLines = linesOf(after)
  const take = Math.min(gutter.count, limit)
  const rows = []
  for (let k = 0; k < take; k += 1) {
    rows.push({
      kind: 'row',
      left: { n: gutter.leftStart + k, text: beforeLines[gutter.leftStart + k - 1], cls: 'ctx' },
      right: { n: gutter.rightStart + k, text: afterLines[gutter.rightStart + k - 1], cls: 'ctx' },
    })
  }
  const rest = gutter.count - take
  return {
    rows,
    rest,
    nextGutter: rest
      ? { kind: 'gutter', count: rest, leftStart: gutter.leftStart + take, rightStart: gutter.rightStart + take }
      : null,
  }
}
