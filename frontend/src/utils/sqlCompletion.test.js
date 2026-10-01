// Ticket 008A / US-15：表名与列名补全的纯逻辑。
//
// CodeMirror 在 happy-dom 里起不来（ADR 0006），所以补全的判定与取数全部放这里测：
// "光标在这儿到底该补表名还是列名"、"表名从 SQL 的哪一段抠出来"、"缓存什么时候失效"。
// SqlEditor.vue 只负责把 completions(doc, caret) 接到 autocompletion 上，不承载判定。
//
// 数据源沿用现有的 dbTables / dbTableSchema，本票不新增后端接口（spec §2）。
// 光标位置一律写 doc.length（文档末尾），别硬编下标——'SELECT * FROM ' 是 14 不是 13。
import { describe, expect, it, vi } from 'vitest'
import { MAX_COMPLETIONS, createCompletionContext, normalizeTables, resolveCompletionTarget } from './sqlCompletion'

// 常用的假 schema：fetch 成 Mock，调用次数就是"有没有命中缓存"的判据。
const schemaOf = (...columns) => () => Promise.resolve({ data: { columns: columns.map((c) => ({ name: c })) } })
const rejectSchema = () => () => Promise.reject(new Error('404 not found'))
const at = (doc) => resolveCompletionTarget(doc, doc.length)

describe('sqlCompletion 目标判定（主流程）', () => {
  it('from 之后补表名：前缀为空，起点就是光标', () => {
    const doc = 'SELECT * FROM '
    expect(at(doc)).toEqual({ kind: 'table', prefix: '', from: doc.length, quoted: false })
  })

  it('已打了几个字母：按输入的前缀过滤', () => {
    const doc = 'SELECT * FROM wid'
    const t = at(doc)
    expect(t.kind).toBe('table')
    expect(t.prefix).toBe('wid')
    // from 指向待替换那段文字的起点，插进去才是替换而不是追加
    expect(doc.slice(t.from, doc.length)).toBe('wid')
  })

  it('表名后面打点：补该表的列名', () => {
    expect(at('SELECT * FROM widgets.')).toMatchObject({ kind: 'column', table: 'widgets', prefix: '' })
  })

  it('列名也带前缀：widgets.n', () => {
    expect(at('SELECT widgets.n')).toMatchObject({ kind: 'column', table: 'widgets', prefix: 'n' })
  })

  it('schema 限定名只取最后一段：public.widgets. 补的是 widgets 的列', () => {
    expect(at('SELECT * FROM public.widgets.')).toMatchObject({ kind: 'column', table: 'widgets' })
  })

  it('未加引号的表名按 PG 规则折叠成小写', () => {
    expect(at('SELECT * FROM WIDGETS.')).toMatchObject({ kind: 'column', table: 'widgets' })
  })

  it('双引号表名原样保留（含分号也不被切断）', () => {
    expect(at('SELECT * FROM "We;ird".')).toMatchObject({ kind: 'column', table: 'We;ird' })
  })

  it('delete from / update / join / into / table 都是表名触发词', () => {
    for (const doc of ['DELETE FROM ', 'UPDATE ', 'LEFT JOIN ', 'INSERT INTO ', 'DROP TABLE ']) {
      expect(at(doc)).toMatchObject({ kind: 'table', prefix: '' })
    }
  })

  it('中文标识符照原样当表名', () => {
    expect(at('SELECT * FROM 订单明')).toMatchObject({ kind: 'table', prefix: '订单明' })
    expect(at('SELECT * FROM 订单明细.')).toMatchObject({ kind: 'column', table: '订单明细' })
  })
})

describe('sqlCompletion 取数与缓存（主流程）', () => {
  it('表名补全来自传入的 tables，不额外发请求', async () => {
    const fetchSchema = vi.fn(schemaOf('id'))
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: [{ name: 'widgets' }], fetchSchema })
    const out = await ctx.completions('SELECT * FROM ')
    expect(out.options.map((o) => o.label)).toEqual(['widgets'])
    expect(fetchSchema).not.toHaveBeenCalled()
  })

  it('列名懒拉一次，第二次命中缓存不再请求；缓存键带 env', async () => {
    const fetchSchema = vi.fn(schemaOf('id', 'name'))
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: [], fetchSchema })
    const doc = 'SELECT * FROM widgets.'
    expect((await ctx.completions(doc)).options.map((o) => o.label)).toEqual(['id', 'name'])
    expect((await ctx.completions(doc)).options.map((o) => o.label)).toEqual(['id', 'name'])
    expect(fetchSchema).toHaveBeenCalledTimes(1)
    expect(fetchSchema).toHaveBeenCalledWith('1', 'dev', 'widgets')
    expect([...ctx.cache.keys()]).toEqual(['dev:widgets'])
  })

  it('切 env 清缓存：同一张表在新环境重新拉', async () => {
    const fetchSchema = vi.fn(schemaOf('id'))
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: [], fetchSchema })
    const doc = 'SELECT * FROM widgets.'
    await ctx.completions(doc)
    ctx.setEnv('prod')
    await ctx.completions(doc)
    expect(fetchSchema).toHaveBeenCalledTimes(2)
    expect(fetchSchema).toHaveBeenLastCalledWith('1', 'prod', 'widgets')
    expect([...ctx.cache.keys()]).toEqual(['prod:widgets'])
  })

  it('换应用同样清缓存：dev 库不是另一个应用的 dev 库', async () => {
    const fetchSchema = vi.fn(schemaOf('id'))
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: [], fetchSchema })
    const doc = 'SELECT * FROM widgets.'
    await ctx.completions(doc)
    ctx.setAppId('2')
    await ctx.completions(doc)
    expect(fetchSchema).toHaveBeenCalledTimes(2)
    expect(fetchSchema).toHaveBeenLastCalledWith('2', 'dev', 'widgets')
  })

  it('同一张表的并发请求只发一次', async () => {
    const fetchSchema = vi.fn(schemaOf('id'))
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: [], fetchSchema })
    const doc = 'SELECT * FROM widgets.'
    const [a, b] = await Promise.all([ctx.completions(doc), ctx.completions(doc)])
    expect(fetchSchema).toHaveBeenCalledTimes(1)
    expect(a.options.map((o) => o.label)).toEqual(b.options.map((o) => o.label))
  })

  it('需要引号的表名，apply 里带引号、label 里不带', async () => {
    const ctx = createCompletionContext({
      appId: '1', env: 'dev', tables: ['We;ird', 'widgets'], fetchSchema: vi.fn(),
    })
    const out = await ctx.completions('SELECT * FROM ')
    expect(out.options.map((o) => o.label)).toEqual(['We;ird', 'widgets'])
    expect(out.options.map((o) => o.apply)).toEqual(['"We;ird"', 'widgets'])
  })

  it('setTables 立刻生效：新表进得来、删掉的表不再出现', async () => {
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: ['widgets'], fetchSchema: vi.fn() })
    expect((await ctx.completions('SELECT * FROM ')).options.map((o) => o.label)).toEqual(['widgets'])
    ctx.setTables([{ name: 'orders' }])
    expect((await ctx.completions('SELECT * FROM ')).options.map((o) => o.label)).toEqual(['orders'])
    ctx.setTables(null)
    expect((await ctx.completions('SELECT * FROM ')).options).toEqual([])
  })
})

describe('sqlCompletion 边界与静默失败', () => {
  it('边界：tables 为空（表还没拉回来）→ 空列表，不抛', async () => {
    const doc = 'SELECT * FROM '
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: [], fetchSchema: vi.fn() })
    expect(await ctx.completions(doc)).toMatchObject({ kind: 'table', from: doc.length, options: [] })
  })

  it('边界：schema 拉取 reject → 列补全为空且**不抛**，失败不入缓存（下次会重试）', async () => {
    const fetchSchema = vi.fn(rejectSchema())
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: [], fetchSchema })
    const doc = 'SELECT * FROM widgets.'
    expect(await ctx.completions(doc)).toMatchObject({ kind: 'column', options: [] })
    expect(ctx.cache.size).toBe(0)
    await ctx.completions(doc)
    expect(fetchSchema).toHaveBeenCalledTimes(2)
  })

  it('边界：fetchSchema 返回非预期结构（没有 columns）→ 视为无列，不抛', async () => {
    const ctx = createCompletionContext({
      appId: '1', env: 'dev', tables: [], fetchSchema: () => Promise.resolve({ data: {} }),
    })
    expect((await ctx.completions('SELECT * FROM widgets.')).options).toEqual([])
  })

  it('边界：columns 是裸字符串数组也吃下', async () => {
    const ctx = createCompletionContext({
      appId: '1', env: 'dev', tables: [], fetchSchema: () => Promise.resolve({ data: { columns: ['id'] } }),
    })
    expect((await ctx.completions('SELECT * FROM widgets.')).options.map((o) => o.label)).toEqual(['id'])
  })

  it('边界：无触发词的位置（select wid）不补，免得好列子被表名糊住', async () => {
    const doc = 'SELECT wid'
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: ['widgets'], fetchSchema: vi.fn() })
    expect(await ctx.completions(doc)).toMatchObject({ kind: null, from: doc.length, options: [] })
  })

  it('边界：空串 / caret 为 0 / 纯空白 → 不补且不抛', async () => {
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: ['widgets'], fetchSchema: vi.fn() })
    expect(resolveCompletionTarget('', 0)).toBe(null)
    expect(resolveCompletionTarget('SELECT', 0)).toBe(null)
    expect((await ctx.completions('')).options).toEqual([])
    expect((await ctx.completions('   ', 1)).options).toEqual([])
  })

  it('边界：caret 越界（超过文档长度 / 负数）按端点钳制', () => {
    const doc = 'SELECT * FROM wid'
    expect(resolveCompletionTarget(doc, 999)).toMatchObject({ kind: 'table', prefix: 'wid' })
    expect(resolveCompletionTarget(doc, -5)).toBe(null)
  })

  it('边界：非数字入参的 caret 当无选区处理，不抛', () => {
    expect(resolveCompletionTarget('SELECT * FROM wid', undefined)).toBe(null)
    expect(resolveCompletionTarget(null, 5)).toBe(null)
  })

  it('边界：紧贴分号（from;）不补；分号前带空格（from ;）照常补', () => {
    expect(resolveCompletionTarget('SELECT * FROM;', 'SELECT * FROM;'.length)).toBe(null)
    expect(resolveCompletionTarget('SELECT * FROM ;', 14)).toMatchObject({ kind: 'table', prefix: '' })
  })

  it('边界：光标在语句中间，只看光标前文（widgets 已存在也要能补新表名）', () => {
    const doc = 'SELECT * FROM widgets WHERE 1=1'
    const caret = doc.indexOf('widgets')
    expect(resolveCompletionTarget(doc, caret)).toMatchObject({ kind: 'table', prefix: '', from: caret })
  })

  it('边界：引号未闭合（FROM "wid）按带引号的前缀处理', () => {
    expect(at('SELECT * FROM "wid')).toMatchObject({ kind: 'table', prefix: 'wid', quoted: true })
  })

  it('边界：多级限定名只认最后一段（a.b.c. 补的是 c 的列）', () => {
    expect(at('SELECT a.b.c.')).toMatchObject({ kind: 'column', table: 'c' })
  })

  it('边界：数字段（SELECT 1.）不抛，最差是没有这张表的空列表', async () => {
    const fetchSchema = vi.fn(rejectSchema())
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: [], fetchSchema })
    expect((await ctx.completions('SELECT 1.')).options).toEqual([])
    expect(fetchSchema).toHaveBeenCalledWith('1', 'dev', '1')
  })

  it('边界：补全项超过上限时截断，不给弹层塞上千行', async () => {
    const many = Array.from({ length: MAX_COMPLETIONS + 50 }, (_, i) => `t_${i}`)
    const ctx = createCompletionContext({ appId: '1', env: 'dev', tables: many, fetchSchema: vi.fn() })
    expect((await ctx.completions('SELECT * FROM ')).options).toHaveLength(MAX_COMPLETIONS)
  })

  it('边界：normalizeTables 吃下各种脏形状', () => {
    expect(normalizeTables(null)).toEqual([])
    expect(normalizeTables(undefined)).toEqual([])
    expect(normalizeTables('widgets')).toEqual([])
    expect(normalizeTables([{ name: 'a' }, 'b', 3, null, {}, { name: '  ' }, { name: 'a' }])).toEqual(['a', 'b'])
  })
})
