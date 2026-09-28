// 语句只读判定（Ticket 002 从 DbAdminView 原语义搬迁，**取严不变**）。
//
// 判定用于决定"这一批要不要二次确认"：只有纯读 SELECT 免确认。
// SELECT ... INTO（建表）与带副作用的函数写法一律归为写。
// 这是**保守白名单**：新出现的可疑写法应落到"需要确认"那一侧，而不是反过来。
const READ_ONLY_FUNCTIONS = /\b(setval|nextval|currval|pg_sleep|pg_terminate_backend|pg_drop_replication_slot|call|perform)\b/i

export function isReadOnlySelect(sql) {
  const s = String(sql ?? '')
  return /^\s*select\b/i.test(s) && !/\binto\b/i.test(s) && !READ_ONLY_FUNCTIONS.test(s)
}
