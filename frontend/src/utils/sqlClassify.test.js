// 语句分类 / 风险标记 / 执行计划（Ticket 007 / US-12、US-13）。
//
// 两套判据**故意不同**，本文件把它们各自钉住：
// - isReadOnlySelect 管「要不要二次确认」，取严——宁可多问一次，可疑写法一律落到确认那一侧；
// - classifyOp 管「徽章显示什么」，与后端 classify_op 一一对齐（权威口径在服务端，审计落库用它）。
// 于是 `SELECT setval('s',1)` 会呈现「徽章 select + 需要确认」的组合，这是设计而非 bug。
//
// 下方 SHARED_CLASSIFY_SAMPLES 是从 tests/test_db_admin.py::test_classify_op_by_first_keyword
// 逐条搬来的**同一份样例**：改一边必须改另一边，否则两处就开始漂移。
import { describe, expect, it } from 'vitest'
import { classifyOp, isReadOnlySelect, planExecution, riskFlags } from './sqlClassify'
import { splitSqlStatements } from './sqlSplit'

const SHARED_CLASSIFY_SAMPLES = [
  ['  SELECT * FROM t', 'select'],
  ['insert into t values (1)', 'insert'],
  ['UPDATE t SET a=1', 'update'],
  ['DELETE FROM t', 'delete'],
  ['CREATE TABLE t(a int)', 'ddl'],
  ['EXPLAIN SELECT 1', 'other'],
]

describe('classifyOp 与后端共享样例（主流程）', () => {
  // 逐条对照：样例文本 + 期望 op 直接进用例名，红了就知道是哪一条漂了
  it.each(SHARED_CLASSIFY_SAMPLES)('classifyOp(%s) = %s', (sql, expected) => {
    expect(classifyOp(sql)).toBe(expected)
  })

  it('五个 DDL 词头全部归 ddl', () => {
    for (const head of ['create', 'alter', 'drop', 'truncate', 'comment']) {
      expect(classifyOp(`${head} TABLE widgets`)).toBe('ddl')
    }
  })

  it('SELECT ... INTO 归 other（它是建表写入，不是读）', () => {
    expect(classifyOp('SELECT * INTO backup_widgets FROM widgets')).toBe('other')
  })

  it('begin/commit/grant/show 这类非五种词头归 other', () => {
    for (const sql of ['BEGIN', 'COMMIT', 'GRANT SELECT ON t TO u', 'SHOW search_path']) {
      expect(classifyOp(sql)).toBe('other')
    }
  })
})

describe('isReadOnlySelect 严格白名单（主流程）', () => {
  it('纯 SELECT 只读、免确认', () => {
    expect(isReadOnlySelect('SELECT 1')).toBe(true)
    expect(isReadOnlySelect('select id, name from widgets where id = 1')).toBe(true)
  })

  it('SELECT ... INTO / setval / call 这三类带副作用的写法一律非只读', () => {
    expect(isReadOnlySelect('SELECT * INTO t2 FROM t1')).toBe(false)
    expect(isReadOnlySelect("SELECT setval('s', 1)")).toBe(false)
    expect(isReadOnlySelect('SELECT nextval(\'s\')')).toBe(false)
    expect(isReadOnlySelect('SELECT pg_sleep(5)')).toBe(false)
    expect(isReadOnlySelect('CALL my_proc()')).toBe(false)
    expect(isReadOnlySelect('SELECT 1; PERFORM something()')).toBe(false)
  })

  it('写/DDL 语句当然不是只读', () => {
    for (const sql of ['UPDATE t SET a=1', 'DELETE FROM t', 'INSERT INTO t VALUES (1)', 'DROP TABLE t']) {
      expect(isReadOnlySelect(sql)).toBe(false)
    }
  })

  it('徽章与门槛的组合被钉住：SELECT setval 是 select 徽章 + 需要确认', () => {
    const sql = "SELECT setval('s', 1)"
    expect(classifyOp(sql)).toBe('select')
    expect(isReadOnlySelect(sql)).toBe(false)
  })
})

describe('riskFlags 高危标记（主流程）', () => {
  it('无 WHERE 的 update / delete 标 no_where', () => {
    expect(riskFlags('UPDATE widgets SET name = 1', 'update')).toEqual(['no_where'])
    expect(riskFlags('DELETE FROM widgets', 'delete')).toEqual(['no_where'])
  })

  it('带 WHERE 的 update / delete 不标', () => {
    expect(riskFlags('UPDATE widgets SET name = 1 WHERE id = 1', 'update')).toEqual([])
    expect(riskFlags('DELETE FROM widgets WHERE id = 1', 'delete')).toEqual([])
  })

  it('ddl 标 ddl', () => {
    expect(riskFlags('DROP TABLE widgets', 'ddl')).toEqual(['ddl'])
    expect(riskFlags('TRUNCATE widgets', 'ddl')).toEqual(['ddl'])
  })

  it('select / insert / other 不标高危', () => {
    expect(riskFlags('SELECT 1', 'select')).toEqual([])
    expect(riskFlags('INSERT INTO widgets VALUES (1)', 'insert')).toEqual([])
    expect(riskFlags('EXPLAIN SELECT 1', 'other')).toEqual([])
  })

  it('省略 op 时按 classifyOp 现算', () => {
    expect(riskFlags('UPDATE widgets SET name = 1')).toEqual(['no_where'])
    expect(riskFlags('CREATE TABLE t(a int)')).toEqual(['ddl'])
  })
})

describe('planExecution 执行计划（主流程）', () => {
  it('全只读段：needConfirm=false，items 带上 op 与 startLine', () => {
    const plan = planExecution([{ sql: 'SELECT 1', startLine: 0 }, { sql: 'SELECT 2', startLine: 1 }], 'dev')
    expect(plan.needConfirm).toBe(false)
    expect(plan.requirePhrase).toBe(false)
    expect(plan.items.map((i) => i.sql)).toEqual(['SELECT 1', 'SELECT 2'])
    expect(plan.items.map((i) => i.op)).toEqual(['select', 'select'])
    expect(plan.items.map((i) => i.startLine)).toEqual([0, 1])
    expect(plan.items.map((i) => i.risk)).toEqual([[], []])
  })

  it('混合段：只要有一条非只读就要确认', () => {
    const plan = planExecution([{ sql: 'SELECT 1' }, { sql: 'INSERT INTO t VALUES (1)' }], 'dev')
    expect(plan.needConfirm).toBe(true)
    expect(plan.requirePhrase).toBe(false)
  })

  it('dev + 写段：needConfirm=true、requirePhrase=false', () => {
    const plan = planExecution([{ sql: 'UPDATE t SET a=1' }], 'dev')
    expect(plan.needConfirm).toBe(true)
    expect(plan.requirePhrase).toBe(false)
    expect(plan.items[0].risk).toEqual(['no_where'])
  })

  it('prod + 写段：requirePhrase=true', () => {
    const plan = planExecution([{ sql: 'UPDATE t SET a=1 WHERE id=1' }], 'prod')
    expect(plan.needConfirm).toBe(true)
    expect(plan.requirePhrase).toBe(true)
    expect(plan.items[0].risk).toEqual([])
  })

  it('prod + 纯只读段：requirePhrase=false（读不需要输确认词）', () => {
    const plan = planExecution([{ sql: 'SELECT 1' }], 'prod')
    expect(plan.needConfirm).toBe(false)
    expect(plan.requirePhrase).toBe(false)
  })

  it('prod + SELECT setval：门槛取严仍要确认词（徽章还是 select）', () => {
    const plan = planExecution([{ sql: "SELECT setval('s', 1)" }], 'prod')
    expect(plan.items[0].op).toBe('select')
    expect(plan.needConfirm).toBe(true)
    expect(plan.requirePhrase).toBe(true)
  })

  it('边界：空数组不抛，needConfirm=false', () => {
    expect(planExecution([], 'prod')).toEqual({ needConfirm: false, requirePhrase: false, items: [] })
  })

  it('边界：纯注释段拆不出语句，与拆分器接上就是一张空计划', () => {
    const stmts = splitSqlStatements('-- 只是注释\n/* 也只是注释 */')
    expect(stmts).toHaveLength(0)
    const plan = planExecution(stmts, 'prod')
    expect(plan.items).toHaveLength(0)
    expect(plan.needConfirm).toBe(false)
    expect(plan.requirePhrase).toBe(false)
  })

  it('边界：非法 env（既不是 dev 也不是 prod）不触发确认词', () => {
    const plan = planExecution([{ sql: 'UPDATE t SET a=1' }], 'staging')
    expect(plan.needConfirm).toBe(true)
    expect(plan.requirePhrase).toBe(false)
  })

  it('边界：env 缺失（undefined/null）按不要求确认词处理，不抛', () => {
    expect(() => planExecution([{ sql: 'UPDATE t SET a=1' }], null)).not.toThrow()
    expect(planExecution([{ sql: 'UPDATE t SET a=1' }], undefined).requirePhrase).toBe(false)
  })

  it('边界：裸字符串数组也能吃下（startLine 缺失记为 null，不是 undefined）', () => {
    const plan = planExecution(['UPDATE t SET a=1'], 'dev')
    expect(plan.items).toEqual([{ sql: 'UPDATE t SET a=1', op: 'update', risk: ['no_where'], startLine: null }])
  })

  it('边界：数组里混入 null / 空对象不会把整批计划打崩，且照样落到确认那一侧', () => {
    const plan = planExecution([null, { }, { sql: '' }, { sql: 'SELECT 1' }], 'dev')
    expect(plan.items).toHaveLength(4)
    // 认不出内容的条目统一归“空串 + other”，不抛也不把后面的条目带崩
    expect(plan.items.map((i) => i.op)).toEqual(['other', 'other', 'other', 'select'])
    expect(plan.items[3].sql).toBe('SELECT 1')
    // 空串不是只读：门槛取严，整批仍要确认
    expect(plan.needConfirm).toBe(true)
  })

  it('边界：纯空白语句不算只读、要确认（保守侧）', () => {
    const plan = planExecution([{ sql: '   ' }], 'dev')
    expect(plan.needConfirm).toBe(true)
  })
})

describe('classifyOp / riskFlags 边界', () => {
  it('空串与全空白不抛，归 other', () => {
    expect(classifyOp('')).toBe('other')
    expect(classifyOp('   \n  ')).toBe('other')
    expect(classifyOp(null)).toBe('other')
    expect(classifyOp(undefined)).toBe('other')
  })

  it('前导换行与制表符一样能认出词头', () => {
    expect(classifyOp('\n\t  select 1')).toBe('select')
    expect(classifyOp('\r\n   DROP TABLE t')).toBe('ddl')
  })

  it('大小写混排与全大写等价', () => {
    expect(classifyOp('CrEaTe TaBlE t(a int)')).toBe('ddl')
    expect(classifyOp('UpDaTe t SET a=1')).toBe('update')
  })

  it('词头后紧跟换行也算同一条语句', () => {
    expect(classifyOp('SELECT\n  1')).toBe('select')
  })

  it('只有词头一个词时不抛', () => {
    expect(classifyOp('SELECT')).toBe('select')
    expect(classifyOp('create')).toBe('ddl')
  })

  it('词头带前导括号/注释时按首 token 判定（与后端一致地归 other）', () => {
    // 后端取的是第一个空白分隔 token，`(select` 不在五种词头里 → other
    expect(classifyOp('(select 1)')).toBe('other')
    expect(classifyOp('-- x\nSELECT 1')).toBe('other')
  })

  it('isReadOnlySelect 边界：空串与纯空白不是只读', () => {
    expect(isReadOnlySelect('')).toBe(false)
    expect(isReadOnlySelect('   ')).toBe(false)
    expect(isReadOnlySelect(null)).toBe(false)
  })

  it('riskFlags 边界：子查询里的 where 会让整条逃过 no_where（已接受的漏报）', () => {
    // 判据是正则 \bwhere\b，不做结构分析：`UPDATE t SET a=(SELECT 1 WHERE x)` 其实全表更新。
    // 工单里明确保守接受，本用例把这条漏报钉住，避免以后有人以为它做了 AST 级判断。
    expect(riskFlags('UPDATE t SET a=(SELECT 1 WHERE x=1)', 'update')).toEqual([])
    expect(riskFlags("UPDATE t SET name='where'", 'update')).toEqual([])
  })

  it('riskFlags 边界：WHERE 在注释里也算命中（同上一条的漏报方向）', () => {
    expect(riskFlags('UPDATE t SET a=1 -- where id=1', 'update')).toEqual([])
  })

  it('riskFlags 边界：未知 op 与空 sql 不抛', () => {
    expect(riskFlags('', 'other')).toEqual([])
    expect(riskFlags('UPDATE t SET a=1', 'weird')).toEqual([])
    expect(riskFlags(null, null)).toEqual([])
  })

  it('边界：中文标识符紧贴 into 时 JS 判为非只读（比后端更严，落在安全侧）', () => {
    // Python 的 \b 认中文为词字符，后端会判 select；JS 的 \w 只认 ASCII，这里判 other。
    // 徽章少一个「select」不伤人，确认门槛多问一次也不伤人——两处都不放松，故不为此上 unicode 正则。
    expect(classifyOp('SELECT * FROM 中文into表')).toBe('other')
    expect(isReadOnlySelect('SELECT * FROM 中文into表')).toBe(false)
  })
})
