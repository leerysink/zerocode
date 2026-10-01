// 语句分类 / 只读判定 / 风险标记 / 执行计划（Ticket 007 / US-12、US-13）。
//
// **两套判据故意不同**，别去"统一"它们：
// - isReadOnlySelect 管「这批要不要二次确认」，取严：宁可多问一次，可疑写法一律落到确认那一侧；
// - classifyOp 管「徽章显示什么」，是后端 `app/services/db_admin_service.classify_op` 的 JS 镜像，
//   权威口径在服务端（审计落库用它），这里只负责展示，不承担安全语义。
// 于是 `SELECT setval('s',1)` 呈现「徽章 select + 需要二次确认」的组合：这是设计，不是 bug。
//
// 与后端保持一致的样例清单在 sqlClassify.test.js 的 SHARED_CLASSIFY_SAMPLES，
// 逐条搬自 tests/test_db_admin.py::test_classify_op_by_first_keyword，改一边必须改另一边。

const DDL_KEYWORDS = new Set(['create', 'alter', 'drop', 'truncate', 'comment'])
// 带副作用的函数式写法：写在 SELECT 里但绝不是"读一下而已"。
// 这是**保守白名单**的一部分——新出现的可疑写法应落到"需要确认"那一侧，而不是反过来。
const SIDE_EFFECT_FUNCTIONS = /\b(setval|nextval|currval|pg_sleep|pg_terminate_backend|pg_drop_replication_slot|call|perform)\b/i

// 取第一个空白分隔的 token 并小写。后端是 `sql.lstrip().lower().split(None, 1)[0]`，
// 语义等价：前导空白（含换行/制表）跳过，词头到下一个空白为止。
function firstKeyword(sql) {
  const m = /^\s*(\S+)/.exec(String(sql ?? ''))
  return m ? m[1].toLowerCase() : ''
}

/**
 * 与后端 classify_op 一一对齐的 JS 镜像，仅用于 op 徽章展示。
 * @returns {'select'|'insert'|'update'|'delete'|'ddl'|'other'}
 */
export function classifyOp(sql) {
  const head = firstKeyword(sql)
  if (DDL_KEYWORDS.has(head)) return 'ddl'
  if (head === 'insert' || head === 'update' || head === 'delete') return head
  if (head === 'select') {
    // SELECT ... INTO 会建表写入，与后端口径一致地不当读处理。
    return /\binto\b/i.test(String(sql ?? '')) ? 'other' : 'select'
  }
  return 'other'
}

/**
 * 是否纯读 SELECT（Ticket 002 从 DbAdminView 原语义搬迁，**取严不变**）。
 *
 * 判定用于决定"这一批要不要二次确认"：只有纯读 SELECT 免确认。
 * SELECT ... INTO（建表）与带副作用的函数写法一律归为写。
 */
export function isReadOnlySelect(sql) {
  const s = String(sql ?? '')
  return /^\s*select\b/i.test(s) && !/\binto\b/i.test(s) && !SIDE_EFFECT_FUNCTIONS.test(s)
}

/**
 * 高危标记：['no_where' | 'ddl']，给确认弹框整行标红用。
 *
 * `no_where` 的判据是正则 `\bwhere\b`，不做结构分析：子查询里的 where、字符串里的
 * 'where'、注释里的 where 都会让它逃过标记。这是工单里明确接受的**漏报**
 * （宁可少标一个红，也不给"已经检查过"的假安全感——真门槛是二次确认本身）。
 *
 * @param {string} sql
 * @param {string} [op] 省略时按 classifyOp 现算
 */
export function riskFlags(sql, op) {
  const kind = op ?? classifyOp(sql)
  const text = String(sql ?? '')
  if (kind === 'ddl') return ['ddl']
  if ((kind === 'update' || kind === 'delete') && !/\bwhere\b/i.test(text)) return ['no_where']
  return []
}

// splitSqlStatements 的返回是 [{ sql, startLine }]；也吃裸字符串数组（调用方想省事时用）。
// 认不出内容的条目统一归一化成空串：不让 null 把整批计划打崩，也不给它"只读"的优待。
function normalizeStatement(item) {
  const raw = typeof item === 'string' ? { sql: item } : (item ?? {})
  return { sql: String(raw.sql ?? ''), startLine: Number.isInteger(raw.startLine) ? raw.startLine : null }
}

/**
 * 一次点击的执行计划：确认弹框与 prod 确认词都照这张表渲染。
 *
 * @param {Array<{sql:string,startLine?:number}|string>} stmts 拆分器给出的语句段
 * @param {string} env 'dev' | 'prod'（其余值按非 prod 处理，不要求确认词）
 * @returns {{needConfirm:boolean, requirePhrase:boolean,
 *            items:Array<{sql:string,op:string,risk:string[],startLine:number|null}>}}
 */
export function planExecution(stmts, env) {
  const items = (Array.isArray(stmts) ? stmts : []).map((item) => {
    const { sql, startLine } = normalizeStatement(item)
    return { sql, op: classifyOp(sql), risk: riskFlags(sql, classifyOp(sql)), startLine }
  })
  return {
    needConfirm: items.some((i) => !isReadOnlySelect(i.sql)),
    // prod 里只要有一条不是纯读，就要人把环境名亲手打一遍（US-13）。
    requirePhrase: env === 'prod' && items.some((i) => !isReadOnlySelect(i.sql)),
    items,
  }
}
