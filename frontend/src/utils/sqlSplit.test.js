import { describe, expect, it } from 'vitest'
import { splitSqlStatements } from './sqlSplit'

// Ticket 002：最小版切段——按裸分号切、trim 后丢弃空段、带 startLine。
// 下面最后一条原本是 002 故意钉住的**已知误切**，Ticket 004 的状态机落地时显式改掉了它，
// 期望值从「切成两条」翻成「切成一条」——留在这里当回归闸，不让它静默漂移回去。
describe('splitSqlStatements 基础切段', () => {
  it('按分号切成多条并按原顺序返回', () => {
    const out = splitSqlStatements('SELECT 1; SELECT 2')
    expect(out.map((s) => s.sql)).toEqual(['SELECT 1', 'SELECT 2'])
  })

  it('尾部分号不产生空段', () => {
    expect(splitSqlStatements('SELECT 1;')).toHaveLength(1)
    expect(splitSqlStatements('SELECT 1;;SELECT 2;')).toHaveLength(2)
  })

  it('边界：空串/纯空白/纯分号都返回空数组', () => {
    expect(splitSqlStatements('')).toEqual([])
    expect(splitSqlStatements('   \n  \t ')).toEqual([])
    expect(splitSqlStatements(';;;')).toEqual([])
  })

  it('边界：单条无分号也返回一条', () => {
    expect(splitSqlStatements('SELECT 1')).toEqual([{ sql: 'SELECT 1', startLine: 1 }])
  })

  it('startLine 是 1 基的原始行号（结果块要说明第几条来自哪里）', () => {
    const out = splitSqlStatements('SELECT 1;\n\nSELECT 2;\nSELECT 3')
    expect(out).toEqual([
      { sql: 'SELECT 1', startLine: 1 },
      { sql: 'SELECT 2', startLine: 3 },
      { sql: 'SELECT 3', startLine: 4 },
    ])
  })

  it('边界：跨行的语句算一条，startLine 取起始行', () => {
    const out = splitSqlStatements('SELECT a,\n  b\nFROM t;\nSELECT 1')
    expect(out).toHaveLength(2)
    expect(out[0].sql).toBe('SELECT a,\n  b\nFROM t')
    expect(out[0].startLine).toBe(1)
    expect(out[1].startLine).toBe(4)
  })

  it('边界：trim 掉首尾空白但不改内部换行', () => {
    const out = splitSqlStatements('  SELECT 1 ;\n  SELECT 2  ')
    expect(out.map((s) => s.sql)).toEqual(['SELECT 1', 'SELECT 2'])
  })

  it('Ticket 004 已改掉：字符串里的分号不再被切（002 的已知误切翻面）', () => {
    const out = splitSqlStatements("SELECT 'a;b'")
    expect(out.map((s) => s.sql)).toEqual(["SELECT 'a;b'"])
  })

  it('边界：null / undefined 入参返回空数组，不抛异常', () => {
    expect(splitSqlStatements(null)).toEqual([])
    expect(splitSqlStatements(undefined)).toEqual([])
  })
})

// ————————————————————————————————————————————————————————————————
// 边界清单（Ticket 004）：假分号一律不切。与上面的基础用例分开放，便于看覆盖率。
// ————————————————————————————————————————————————————————————————
describe('splitSqlStatements 状态机边界：引号内的假分号', () => {
  it('单引号串里的分号不算结束：SELECT \'a;b\' AS x; SELECT 2 恰好两条', () => {
    const out = splitSqlStatements("SELECT 'a;b' AS x; SELECT 2")
    expect(out.map((s) => s.sql)).toEqual(["SELECT 'a;b' AS x", 'SELECT 2'])
  })

  it("边界：连续两个单引号是转义不是闭合（'it''s;ok' 仍是一条）", () => {
    const out = splitSqlStatements("SELECT 'it''s;ok' AS x")
    expect(out.map((s) => s.sql)).toEqual(["SELECT 'it''s;ok' AS x"])
  })

  it('边界：双引号标识符里的分号不切（"we;ird"; SELECT 1 两条）', () => {
    const out = splitSqlStatements('SELECT "we;ird" FROM t; SELECT 1')
    expect(out.map((s) => s.sql)).toEqual(['SELECT "we;ird" FROM t', 'SELECT 1'])
  })

  it('边界：连续两个双引号是转义标识符（"a""b;c" 仍是一条）', () => {
    const out = splitSqlStatements('SELECT "a""b;c" AS x')
    expect(out.map((s) => s.sql)).toEqual(['SELECT "a""b;c" AS x'])
  })
})

describe('splitSqlStatements 状态机边界：注释里的假分号', () => {
  it('行注释里的分号不切，注释原文留在语句上', () => {
    const out = splitSqlStatements('-- ;\nSELECT 1')
    expect(out).toEqual([{ sql: '-- ;\nSELECT 1', startLine: 1 }])
  })

  it('块注释里的分号不切', () => {
    const out = splitSqlStatements('/* ; */ SELECT 1;')
    expect(out.map((s) => s.sql)).toEqual(['/* ; */ SELECT 1'])
  })

  it('边界：PG 允许块注释嵌套，内层 /* ; */ 提前闭合不能把外层算结束', () => {
    const out = splitSqlStatements('/* a /* b ; */ ; */ SELECT 1')
    expect(out.map((s) => s.sql)).toEqual(['/* a /* b ; */ ; */ SELECT 1'])
  })

  it('边界：块注释里可以放行注释起始符（/* -- */ 结束后才谈拆分）', () => {
    const out = splitSqlStatements('/* -- */ SELECT 1; SELECT 2')
    expect(out.map((s) => s.sql)).toEqual(['/* -- */ SELECT 1', 'SELECT 2'])
  })

  it('边界：行注释里有单引号（don\'t）不能被当成字符串起头', () => {
    const out = splitSqlStatements("-- don't; 这条不执行\nSELECT 1")
    expect(out.map((s) => s.sql)).toEqual(["-- don't; 这条不执行\nSELECT 1"])
  })

  it('边界：纯注释段被丢弃，不产出可执行项', () => {
    expect(splitSqlStatements('-- hi').map((s) => s.sql)).toEqual([])
    expect(splitSqlStatements('/* c */').map((s) => s.sql)).toEqual([])
    expect(splitSqlStatements('-- hi\n; SELECT 1').map((s) => s.sql)).toEqual(['SELECT 1'])
    expect(splitSqlStatements('/* c */ ; SELECT 2 ; -- tail').map((s) => s.sql)).toEqual(['SELECT 2'])
  })

  it('边界：注释与语句同段时不剔注释（拆分段只认分号，剔注释是 PG 的事）', () => {
    // `-- hi;` 的分号在行注释里，整段就没有拆分点：一条语句，注释原文一并送后端。
    expect(splitSqlStatements('-- hi;\nSELECT 1')).toEqual([{ sql: '-- hi;\nSELECT 1', startLine: 1 }])
  })

  it('边界：行注释一直吃到行尾，同行后面的文字全算注释（-- hi; SELECT 1 一条也没有）', () => {
    // PG 语义如此：`--` 后没有换行就没有“后面的代码”，不能把 SELECT 1 抖出来执行。
    expect(splitSqlStatements('-- hi; SELECT 1')).toEqual([])
  })
})

describe('splitSqlStatements 状态机边界：dollar-quote', () => {
  it('$$ 函数体里的分号不切：建函数 + SELECT 恰好两条', () => {
    const fn = "CREATE FUNCTION zc_add(a int, b int) RETURNS int AS $$ BEGIN RETURN a + b; END; $$ LANGUAGE plpgsql"
    const out = splitSqlStatements(`${fn}; SELECT 1`)
    expect(out.map((s) => s.sql)).toEqual([fn, 'SELECT 1'])
  })

  it('边界：具名标签 $body$ ... $body$ 同样不切', () => {
    const out = splitSqlStatements('CREATE FUNCTION f() RETURNS void AS $body$ BEGIN PERFORM 1; END; $body$ LANGUAGE plpgsql;')
    expect(out).toHaveLength(1)
    expect(out[0].sql).toBe('CREATE FUNCTION f() RETURNS void AS $body$ BEGIN PERFORM 1; END; $body$ LANGUAGE plpgsql')
  })

  it('边界：dollar-quote 内部允许裸单引号与分号混排', () => {
    const out = splitSqlStatements("SELECT $$ it's ; here $$ AS x;")
    expect(out.map((s) => s.sql)).toEqual(["SELECT $$ it's ; here $$ AS x"])
  })

  it('边界：$1 是参数占位符而不是 dollar-quote 起头，不能吞掉后文', () => {
    const out = splitSqlStatements('SELECT $1; SELECT $2')
    expect(out.map((s) => s.sql)).toEqual(['SELECT $1', 'SELECT $2'])
  })
})

describe('splitSqlStatements 状态机边界：未闭合时降级不抛异常', () => {
  it('未闭合单引号 → 整段原样一条（按钮不该失灵）', () => {
    expect(splitSqlStatements("SELECT 'a;b").map((s) => s.sql)).toEqual(["SELECT 'a;b"])
  })

  it('未闭合 $$ → 整段原样一条', () => {
    expect(splitSqlStatements('SELECT $$ a; b').map((s) => s.sql)).toEqual(['SELECT $$ a; b'])
  })

  it('未闭合块注释 → 整段原样一条，不能被当成纯注释丢弃', () => {
    expect(splitSqlStatements('SELECT 1 /* 说一半').map((s) => s.sql)).toEqual(['SELECT 1 /* 说一半'])
  })

  it('边界：未闭合只降级当前段，前面已经闭合的语句照常保留', () => {
    expect(splitSqlStatements("SELECT 1; SELECT 'x").map((s) => s.sql)).toEqual(['SELECT 1', "SELECT 'x"])
  })
})

describe('splitSqlStatements 状态机边界：中文全角与跨行行号', () => {
  it('全角分号不是语句结束符', () => {
    const out = splitSqlStatements("SELECT '中文；全角' AS a; SELECT 'b'")
    expect(out.map((s) => s.sql)).toEqual(["SELECT '中文；全角' AS a", "SELECT 'b'"])
  })

  it('边界：字符串内部的换行要计入行号，后续语句的 startLine 不能少算', () => {
    const out = splitSqlStatements("SELECT 'a\nb;\nc';\nSELECT 1")
    expect(out).toEqual([
      { sql: "SELECT 'a\nb;\nc'", startLine: 1 },
      { sql: 'SELECT 1', startLine: 4 },
    ])
  })

  it('边界：注释内部的换行同样计入 startLine', () => {
    // 行分布：1 `/*` / 2 空 / 3 ` 多行注释` / 4 `*/ SELECT 1;` / 5 `SELECT 2`
    const out = splitSqlStatements('/*\n\n 多行注释\n*/ SELECT 1;\nSELECT 2')
    expect(out).toEqual([
      { sql: '/*\n\n 多行注释\n*/ SELECT 1', startLine: 1 },
      { sql: 'SELECT 2', startLine: 5 },
    ])
  })
})

describe('splitSqlStatements 状态机边界：大文本不塌成平方', () => {
  it('200KB 量级（近两万条）拆分在 1 秒内完成且条数精确', () => {
    const text = "SELECT 'a;b';\n".repeat(18000) // 每条带一个假分号 + 一个真结束分号，13 字符 ≈ 230KB
    const startedAt = Date.now()
    const out = splitSqlStatements(text)
    const cost = Date.now() - startedAt
    expect(out).toHaveLength(18000)
    expect(out[0].sql).toBe("SELECT 'a;b'")
    expect(out[17999].startLine).toBe(18000) // 一行一条，第 k 条落在第 k+1 行
    expect(cost).toBeLessThan(1000)
  })
})
