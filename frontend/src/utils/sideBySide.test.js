// 双栏对齐模型的纯函数测试。
// 夹具一律由后端同款调用真实产出：
//   difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
//                        fromfile='a/<path>', tofile='b/<path>')   （app/agent/tools.py:_unified_diff）
// 手写「我以为 unified diff 长这样」会把 difflib 的真实怪癖（无尾换行时的合并行、@@ 区间口径）
// 编码成错误前提，所以每条夹具都是跑出来的，不是照记忆敲的。
import { describe, expect, it } from 'vitest'
import { buildSideBySide, expandGutter } from './sideBySide'

// 10 行文件改第 4 行：默认 3 行上下文，所以 hunk 只覆盖 1..7，尾部 8..10 是未变更段
const ONE_REPLACE =
  '--- a/src/App.vue\n+++ b/src/App.vue\n@@ -1,7 +1,7 @@\n l1\n l2\n l3\n-l4\n+CHANGED\n l5\n l6\n l7\n'
const BEFORE_10 = 'l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\nl9\nl10\n'
const AFTER_10 = 'l1\nl2\nl3\nCHANGED\nl5\nl6\nl7\nl8\nl9\nl10\n'

const row = (ln, lt, lc, rn, rt, rc) => ({
  kind: 'row',
  left: { n: ln, text: lt, cls: lc },
  right: { n: rn, text: rt, cls: rc },
})
const gap = { n: null, text: '', cls: 'gap' }

describe('buildSideBySide 单 hunk 一处修改', () => {
  it('上下文两侧成对；替换块左右同行对齐（VS Code 口径）；hunk 之后的行折成未变更段', () => {
    const model = buildSideBySide({ diff: ONE_REPLACE, before: BEFORE_10, after: AFTER_10 })

    expect(model.status).toBe('ok')
    expect(model.added).toBe(1)
    expect(model.removed).toBe(1)
    expect(model.rows).toEqual([
      row(1, 'l1', 'ctx', 1, 'l1', 'ctx'),
      row(2, 'l2', 'ctx', 2, 'l2', 'ctx'),
      row(3, 'l3', 'ctx', 3, 'l3', 'ctx'),
      // 删除行与新增行落在同一行网格上：左栏红、右栏绿，行号各自按本侧累计
      row(4, 'l4', 'del', 4, 'CHANGED', 'add'),
      row(5, 'l5', 'ctx', 5, 'l5', 'ctx'),
      row(6, 'l6', 'ctx', 6, 'l6', 'ctx'),
      row(7, 'l7', 'ctx', 7, 'l7', 'ctx'),
      { kind: 'gutter', count: 3, leftStart: 8, rightStart: 8 },
    ])
  })
})

// difflib.unified_diff('', 'only\n') 的真实产物。`-0,0` 是「旧文件一行都没有，新内容插在第 1 行之前」，
// 行号基准一旦被当成 0-1=-1，尾部就会凭空多出一条「1 行未变更」的折叠段。
const CREATE_ONE =
  '--- a/x.txt\n+++ b/x.txt\n@@ -0,0 +1 @@\n+only\n'

describe('buildSideBySide 新建文件', () => {
  it('`@@ -0,0 +1 @@` 只给一行新增，左栏整行空缺，且不产生任何折叠段', () => {
    const model = buildSideBySide({ diff: CREATE_ONE, before: '', after: 'only\n' })

    expect(model.status).toBe('ok')
    expect(model.added).toBe(1)
    expect(model.removed).toBe(0)
    expect(model.rows).toEqual([
      { kind: 'row', left: gap, right: { n: 1, text: 'only', cls: 'add' } },
    ])
  })
})

// difflib.unified_diff('a\nb', 'a\nB')（两侧都**没有**行尾换行）的真实产物：
// 最后一个 hunk 正文是 `-b+B` —— 旧行与新行被挤在同一行里，因为 difflib 按 keepends 拼接、
// 而这两行都不带 \n。按前缀切会得到「删了 b+B、没加任何行」，与全文对不上。
// 这种形状无法从 diff 文本还原（丢了换行边界），只能如实报「对不齐」，由页面退回全文双栏。
const MERGED_NO_TRAILING =
  '--- a/x.txt\n+++ b/x.txt\n@@ -1,2 +1,2 @@\n a\n-b+B'

describe('buildSideBySide 与全文回验', () => {
  it('diff 解析结果与 before/after 全文对不上时报 unaligned，不铺一套假对齐的行', () => {
    const model = buildSideBySide({
      diff: MERGED_NO_TRAILING, before: 'a\nb', after: 'a\nB',
    })

    expect(model.status).toBe('unaligned')
    expect(model.rows).toEqual([])
  })

  // difflib.unified_diff('a\r\nb\r\nc\r\n', 'a\r\nB\r\nc\r\n') 的真实产物：
  // 正文行带着 \r。回验是拿 JS split('\n') 数的全文，CRLF 两侧同样是 'a\r' 这种带 \r 的元素，
  // 必须对得上——否则每个 Windows 换行的文件都会掉进 unaligned，降级就成了常态。
  it('CRLF 文件不因行尾的 \\r 被误判成对不齐', () => {
    const model = buildSideBySide({
      diff: '--- a/x.txt\n+++ b/x.txt\n@@ -1,3 +1,3 @@\n a\r\n-b\r\n+B\r\n c\r\n',
      before: 'a\r\nb\r\nc\r\n',
      after: 'a\r\nB\r\nc\r\n',
    })

    expect(model.status).toBe('ok')
    expect(model.rows).toEqual([
      row(1, 'a\r', 'ctx', 1, 'a\r', 'ctx'),
      row(2, 'b\r', 'del', 2, 'B\r', 'add'),
      row(3, 'c\r', 'ctx', 3, 'c\r', 'ctx'),
    ])
  })
})

// difflib.unified_diff('-- x\n++ y\nreal\n', '-- x\nCHANGED\n') 的真实产物：
// `-++ y` 是「原文 `++ y` 的删除行」，` -- x` 是上下文。前缀只有一个字符，判类型只看它，
// 不能像 DiffBlock 那样按「形如 --- /+++ 且在前两行」猜头行（那是它已知的残余限制，这里不继承）。
const PLUS_MINUS_BODY =
  '--- a/x.txt\n+++ b/x.txt\n@@ -1,3 +1,2 @@\n -- x\n-++ y\n-real\n+CHANGED\n'

describe('buildSideBySide 正文里的 +/- 形状', () => {
  it('原文形如 `++ y` 的删除行按前缀判为删除，不当成文件头或新增丢掉', () => {
    const model = buildSideBySide({
      diff: PLUS_MINUS_BODY, before: '-- x\n++ y\nreal\n', after: '-- x\nCHANGED\n',
    })

    expect(model.status).toBe('ok')
    expect(model.rows).toEqual([
      row(1, '-- x', 'ctx', 1, '-- x', 'ctx'),
      row(2, '++ y', 'del', 2, 'CHANGED', 'add'),
      row(3, 'real', 'del', null, '', 'gap'),
    ])
    expect(model.added).toBe(1)
    expect(model.removed).toBe(2)
  })
})

describe('buildSideBySide 折叠未变更段', () => {
  it('首个 hunk 之前的行折成段首折叠（@@ -4 起 → 前 3 行未变更）', () => {
    const before = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((i) => `L${String(i).padStart(2, '0')}\n`).join('')
    const after = before.replace('L07\n', 'X\n')
    const model = buildSideBySide({
      diff: '--- a/x.txt\n+++ b/x.txt\n@@ -4,7 +4,7 @@\n L04\n L05\n L06\n-L07\n+X\n L08\n L09\n L10\n',
      before,
      after,
    })

    expect(model.rows[0]).toEqual({ kind: 'gutter', count: 3, leftStart: 1, rightStart: 1 })
  })

  it('两个 hunk 之间的段落折成一条折叠，段里没有变更行', () => {
    const before = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
      .map((i) => `L${String(i).padStart(2, '0')}\n`).join('')
    const after = before.replace('L03\n', 'X\n').replace('L17\n', 'X\n')
    const model = buildSideBySide({
      diff: '--- a/x.txt\n+++ b/x.txt\n@@ -1,6 +1,6 @@\n L01\n L02\n-L03\n+X\n L04\n L05\n L06\n'
        + '@@ -14,7 +14,7 @@\n L14\n L15\n L16\n-L17\n+X\n L18\n L19\n L20\n',
      before,
      after,
    })

    // 第一 hunk 覆盖 1..6，第二 hunk 从 14 起 → 中间 7..13 共 7 行
    expect(model.rows.filter((r) => r.kind === 'gutter')).toEqual([
      { kind: 'gutter', count: 7, leftStart: 7, rightStart: 7 },
    ])
  })
})

describe('buildSideBySide 替换不平衡', () => {
  it('删 2 行只加 1 行时，多出来的删除行右栏补 gap（两侧网格仍逐行对齐）', () => {
    const model = buildSideBySide({
      diff: '--- a/x.txt\n+++ b/x.txt\n@@ -1,4 +1,3 @@\n k1\n-D1\n-D2\n+A1\n k2\n',
      before: 'k1\nD1\nD2\nk2\n',
      after: 'k1\nA1\nk2\n',
    })

    expect(model.status).toBe('ok')
    expect(model.rows).toEqual([
      row(1, 'k1', 'ctx', 1, 'k1', 'ctx'),
      row(2, 'D1', 'del', 2, 'A1', 'add'),
      row(3, 'D2', 'del', null, '', 'gap'),
      row(4, 'k2', 'ctx', 3, 'k2', 'ctx'),
    ])
  })
})

describe('buildSideBySide 删除整个文件', () => {
  it('`+0,0` 时右栏整列空缺，行号与全文都对得上', () => {
    const model = buildSideBySide({
      diff: '--- a/x.txt\n+++ b/x.txt\n@@ -1,2 +0,0 @@\n-line1\n-line2\n',
      before: 'line1\nline2\n',
      after: '',
    })

    expect(model.status).toBe('ok')
    expect(model.added).toBe(0)
    expect(model.removed).toBe(2)
    expect(model.rows).toEqual([
      row(1, 'line1', 'del', null, '', 'gap'),
      row(2, 'line2', 'del', null, '', 'gap'),
    ])
  })
})

// 20 行的 L01..L20，与上面「两个 hunk 之间 7 行未变更」那条夹具同源
const TANK = {
  before: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    .map((i) => `L${String(i).padStart(2, '0')}\n`).join(''),
  after: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    .map((i) => (i === 3 || i === 17 ? 'X\n' : `L${String(i).padStart(2, '0')}\n`)).join(''),
}
const GUTTER_7 = { kind: 'gutter', count: 7, leftStart: 7, rightStart: 7 }

describe('buildSideBySide 写入内容与上一版一致', () => {
  it('diff 为空（difflib 对相等输入不吐任何行）时报 identical，而不是铺一整篇折叠段', () => {
    const model = buildSideBySide({ diff: '', before: 'a\nb\n', after: 'a\nb\n' })

    expect(model.status).toBe('identical')
    expect(model.rows).toEqual([])
  })
})

describe('expandGutter 展开折叠段', () => {
  it('折叠段展开后两侧都是上下文行，行号从段首起各自递增，取全文真原文', () => {
    const { rows, rest, nextGutter } = expandGutter(GUTTER_7, TANK, 500)

    expect(rest).toBe(0)
    expect(nextGutter).toBe(null)
    expect(rows).toEqual([
      row(7, 'L07', 'ctx', 7, 'L07', 'ctx'),
      row(8, 'L08', 'ctx', 8, 'L08', 'ctx'),
      row(9, 'L09', 'ctx', 9, 'L09', 'ctx'),
      row(10, 'L10', 'ctx', 10, 'L10', 'ctx'),
      row(11, 'L11', 'ctx', 11, 'L11', 'ctx'),
      row(12, 'L12', 'ctx', 12, 'L12', 'ctx'),
      row(13, 'L13', 'ctx', 13, 'L13', 'ctx'),
    ])
  })

  it('超过上限只铺一部分，剩下的还给一条能继续点的折叠段（一万行未变更不能一次冻住页面）', () => {
    const { rows, rest, nextGutter } = expandGutter(GUTTER_7, TANK, 3)

    expect(rows.length).toBe(3)
    expect(rest).toBe(4)
    expect(nextGutter).toEqual({ kind: 'gutter', count: 4, leftStart: 10, rightStart: 10 })
  })
})
