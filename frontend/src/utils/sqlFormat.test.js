// SQL 格式化纯函数（Ticket 006 / US-02）。
//
// 契约（工单 006 已裁定，另见 docs/specs 的 US-02）：
// - 无选区格式化全段；有选区只格式化选中片段并**原地替换**，返回重排后的新选区边界。
// - 只有 sql-formatter 抛语法错（未闭合引号 / 未闭合括号这类 token 级错误）才回退原文，
//   带 kind='error' 供调用方轻提示；不抛的残缺片段（`select from`）照它的结果重排。
// - 选区内部的**首尾空白原样保留**，只重排中间核心：sql-formatter 会把整段 trim 掉，
//   直接端上去会吃掉用户用来分隔语句的空行。
//
// CodeMirror 在 happy-dom 里起不来，所以格式化逻辑必须能像这样脱离编辑器测；
// SqlEditor 只提供选区读数与写回通道，SqlConsole 只做接线。
import { describe, expect, it } from 'vitest'
import { format } from 'sql-formatter'
import { FORMAT_OPTIONS, formatRange } from './sqlFormat'

const fmt = (s) => format(s, FORMAT_OPTIONS)

describe('sqlFormat 格式化（主流程）', () => {
  it('无选区格式化全段：单行乱排变多行缩进、关键字大写', () => {
    const res = formatRange('select id,name from widgets where id=1 order by id desc limit 10', null, null)
    expect(res.ok).toBe(true)
    expect(res.kind).toBe('ok')
    expect(res.text.startsWith('SELECT\n')).toBe(true)
    expect(res.text).toContain('ORDER BY\n  id DESC')
    expect(res.text).toContain('LIMIT\n  10')
    // 标识符不能被一起大写
    expect(res.text).toContain('widgets')
  })

  it('无选区时返回的选区边界为 null：调用方不该重设光标', () => {
    const res = formatRange('select 1', 5, 5)
    expect(res.selStart).toBeNull()
    expect(res.selEnd).toBeNull()
    expect(res.text).toBe('SELECT\n  1')
  })

  it('有选区只重排选中片段，选区外的前后文一字不动', () => {
    const doc = 'select 1;\nselect a,b from t;\nselect 2;'
    const from = doc.indexOf('select a')
    const to = doc.indexOf('t;') + 2
    const res = formatRange(doc, from, to)
    expect(res.text.startsWith('select 1;\n')).toBe(true)
    expect(res.text.endsWith('\nselect 2;')).toBe(true)
    expect(res.inserted).toBe(fmt('select a,b from t;'))
    // 返回的边界必须正好框住重排后的核心，调用方据此保持"选中的那段仍被选中"
    expect(res.text.slice(res.selStart, res.selEnd)).toBe(res.inserted)
  })

  it('选区覆盖多条语句：每一条都被重排且分号保留', () => {
    const doc = 'select 1;\nselect a from t;\nselect b from u;\nselect 2;'
    const from = doc.indexOf('select a')
    const to = doc.indexOf('select b') + 'select b from u;'.length
    const res = formatRange(doc, from, to)
    expect(res.text.startsWith('select 1;\n')).toBe(true)
    expect(res.text.endsWith('\nselect 2;')).toBe(true)
    expect(res.inserted).toContain('SELECT\n  a\nFROM\n  t;')
    expect(res.inserted).toContain('SELECT\n  b\nFROM\n  u;')
  })

  it('重复格式化幂等：再按一次不产生二次变化', () => {
    const doc = "select id, name from widgets where status = 'ok' order by id desc"
    const once = formatRange(doc, null, null)
    const twice = formatRange(once.text, null, null)
    expect(twice.ok).toBe(true)
    expect(twice.text).toBe(once.text)
  })

  it('中文列名与中文引号标识符不被破坏', () => {
    expect(formatRange("select 订单号, 客户名称 from 订单表 where 状态='已完成'", null, null).text)
      .toBe("SELECT\n  订单号,\n  客户名称\nFROM\n  订单表\nWHERE\n  状态 = '已完成'")
    expect(formatRange('select "订单号" from "订单表"', null, null).text)
      .toBe('SELECT\n  "订单号"\nFROM\n  "订单表"')
  })

  it('返回实际替换区间 from/to：调用方照它写回，不必自己判"无选区就是整段"', () => {
    const doc = 'select 1;\nselect 2;'
    const at = doc.indexOf('select 2')
    expect([formatRange(doc, null, null).from, formatRange(doc, null, null).to]).toEqual([0, doc.length])
    expect([formatRange(doc, at, doc.length).from, formatRange(doc, at, doc.length).to]).toEqual([at, doc.length])
    // 越界/倒置的选区回抛**钳制交换后**的区间，拿它去替换才不会越界
    expect([formatRange(doc, 3, doc.length + 50).from, formatRange(doc, 3, doc.length + 50).to]).toEqual([3, doc.length])
    expect([formatRange(doc, 3, 0).from, formatRange(doc, 3, 0).to]).toEqual([0, 3])
  })
})

describe('sqlFormat 边界', () => {
  it('边界：选区跨一条语句中部，只动选中那截', () => {
    const doc = 'SELECT 1\nfrom widgets where id=1\nORDER BY 1'
    const from = doc.indexOf('from widgets')
    const to = doc.indexOf('id=1') + 4
    const res = formatRange(doc, from, to)
    expect(res.inserted).toBe(fmt('from widgets where id=1'))
    expect(res.text.startsWith('SELECT 1\n')).toBe(true)
    expect(res.text.endsWith('\nORDER BY 1')).toBe(true)
  })

  it('边界：选区首尾空白原样保留，只重排中间核心', () => {
    // 选区框住 '   select 2   '：sql-formatter 会把整段 trim，直接端上去会吃掉这三个空格
    const doc = 'SELECT 1;\n   select 2   \nSELECT 3;'
    const from = doc.indexOf('   select 2')
    const to = from + '   select 2   '.length
    const res = formatRange(doc, from, to)
    expect(res.text).toBe('SELECT 1;\n   SELECT\n  2   \nSELECT 3;')
    // 返回的边界只框重排后的核心，把首尾空白留在选区外
    expect(res.text.slice(from, res.selStart)).toBe('   ')
    expect(res.text.slice(res.selEnd, res.selEnd + 3)).toBe('   ')
  })

  it('边界：全段格式化也不吃掉首尾空行（无选区与有选区同一条路径）', () => {
    const res = formatRange('\n\nselect 1\n', null, null)
    expect(res.text).toBe('\n\nSELECT\n  1\n')
    expect(res.inserted).toBe(res.text)
  })

  it('边界：选中纯空白返回 empty 且文本一字不变', () => {
    const doc = 'select 1;\n   \nselect 2;'
    const from = doc.indexOf('\n') + 1
    const to = from + 3
    const res = formatRange(doc, from, to)
    expect(res.kind).toBe('empty')
    expect(res.ok).toBe(false)
    expect(res.text).toBe(doc)
    expect(res.inserted).toBe('')
    expect(res.selStart).toBeNull()
  })

  it('边界：未闭合引号格式化失败，回退原文并标 error（不抛异常）', () => {
    const doc = "SELECT 'unterminated"
    let res
    expect(() => { res = formatRange(doc, null, null) }).not.toThrow()
    expect(res.kind).toBe('error')
    expect(res.ok).toBe(false)
    expect(res.text).toBe(doc)
    expect(res.inserted).toBe('')
  })

  it('边界：未闭合括号同样回退原文', () => {
    const doc = 'select a from t where b in (1,2'
    const res = formatRange(doc, null, null)
    expect(res.kind).toBe('error')
    expect(res.text).toBe(doc)
  })

  it('边界：残缺但语法可解析的片段照常重排、不吞输入', () => {
    // sql-formatter 对 select from 不抛错（只吐成 SELECT\nFROM），按裁定的口径就用它的结果
    const res = formatRange('select from', null, null)
    expect(res.kind).toBe('ok')
    expect(res.text).toBe('SELECT\nFROM')
  })

  it('边界：纯注释段原样返回，不抛也不清空', () => {
    const res = formatRange('-- 说明', null, null)
    expect(res.kind).toBe('ok')
    expect(res.text).toBe('-- 说明')
  })

  it('边界：空文本 / 非字符串文本返回 empty 且不抛', () => {
    for (const v of ['', '   ', null, undefined, 0]) {
      expect(() => formatRange(v, null, null)).not.toThrow()
      expect(formatRange(v, null, null).kind).toBe('empty')
      expect(formatRange(v, null, null).text).toBe(typeof v === 'string' ? v : '')
    }
  })

  it('边界：选区下标越界被钳制（不会把选区外的文本卷进来）', () => {
    const doc = 'select 1; select 2;'
    const res = formatRange(doc, doc.indexOf('select 2'), doc.length + 50)
    expect(res.inserted).toBe(fmt('select 2;'))
    expect(res.text).toBe('select 1; ' + fmt('select 2;'))
  })

  it('边界：起止倒置的选区自动交换', () => {
    const doc = 'select 1; select 2;'
    const swapped = formatRange(doc, 9, 0)
    const normal = formatRange(doc, 0, 9)
    expect(swapped.text).toBe(normal.text)
    expect(swapped.text).toBe(`${fmt('select 1;')} select 2;`)
  })

  it('边界：非整数选区（NaN / 字符串）按无选区处理走全段', () => {
    const doc = 'select 1;'
    for (const bad of [NaN, 'a', {}, Infinity]) {
      const res = formatRange(doc, bad, bad)
      expect(res.kind).toBe('ok')
      expect(res.text).toBe(fmt(doc))
      expect(res.selStart).toBeNull()
    }
  })

  it('边界：empty/error 的替换区间收成 0-0，误调写回也不会吃掉一个字符', () => {
    for (const doc of ['', '   ', "SELECT 'unterminated"]) {
      const res = formatRange(doc, 0, doc.length)
      expect(res.ok).toBe(false)
      expect([res.from, res.to, res.inserted]).toEqual([0, 0, ''])
    }
  })

  it('边界：只有负下标的选区也不会越界取到 undefined 片段', () => {
    const doc = 'select 1; select 2;'
    const res = formatRange(doc, -10, doc.indexOf('select 1;') + 9)
    expect(res.text).toContain(fmt('select 1;'))
    expect(res.text).toContain('select 2;')
  })
})
