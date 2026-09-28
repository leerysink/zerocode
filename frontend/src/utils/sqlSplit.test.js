import { describe, expect, it } from 'vitest'
import { splitSqlStatements } from './sqlSplit'

// Ticket 002：最小版切段——按裸分号切、trim 后丢弃空段、带 startLine。
// 引号/注释/dollar-quote 里的假分本票**不**识别（那是 Ticket 004 的状态机），
// 因此下面有一条用例故意把当前误切行为钉住：004 落地时必须显式改掉它，而不是让它静默漂移。
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

  it('已知限制（Ticket 004 会改掉）：字符串里的分号仍被切', () => {
    const out = splitSqlStatements("SELECT 'a;b'")
    expect(out.map((s) => s.sql)).toEqual(["SELECT 'a", "b'"])
  })
})
