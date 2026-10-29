# schema —— 结构真相（声明式·当前快照）

- `schema.sql` 是应用**表结构的唯一真相**：一份全量、幂等、可重放的 DDL，只描述「结构最终长什么样」，**不含任何数据**
- 改表结构 = **编辑本文件的目标态**，不再手写/回改结构增量文件；dev 预览与 prod 发布都会全量 fold 这一份，两边收敛到同一目标态 → 结构等价由构造保证
- 只允许幂等可重放语句：
  - `CREATE TABLE IF NOT EXISTS t (col type ...);` 建新表
  - `ALTER TABLE t ADD COLUMN IF NOT EXISTS col type ...;` 给已存在的表补列（**新列必须写成这种独立语句**，`CREATE TABLE IF NOT EXISTS` 不会给已存在的表加列）
  - **补列红线**：本文件每次预览/发布都全量重放，给**已有数据**的表补列时裸写 `NOT NULL` 必被 PG 拒（`column "x" of relation "t" contains null values`），且 fold 不进 history → 之后每次重启都红在同一行、栈永久起不来。新列要么写成 `... NOT NULL DEFAULT <值>`（该列要求唯一时默认值取 `gen_random_uuid()::text`，常量默认值会让存量行撞唯一索引），要么只补**可空**列、把值交给 `sqlchange/` 数据轨回填
  - **唯一索引红线**：给**已有数据**的表加 `CREATE UNIQUE INDEX`（或唯一约束）前，先确认存量值没有重复；撞重复时 PG 红在 `could not create unique index "..."` / `DETAIL: Key (col)=() is duplicated.`（`()` 是**空串**不是 NULL——普通唯一索引允许多个 NULL，但不允许多个空串），且 fold 不进 history → 之后每次重启红在同一行、栈永久起不来。存量可能有重复时拆两步：本版只把清理/回填写进 `sqlchange/D*.sql`，下个版本再在本文件加唯一索引（fold 先于数据轨，同一次启动里索引会跑在清理之前）。「可选」的列不要用常量默认值（尤其 `''`）占位，未填写 NULL
  - `CREATE INDEX IF NOT EXISTS i ON t (...);` 建索引
- **禁止** `DROP` / `ALTER COLUMN TYPE`（属破坏性变更，见下）/ `ALTER COLUMN ... SET NOT NULL`（fold 先于数据轨执行，同一次重启里它跑在回填之前必炸）
- 注：`CREATE TABLE` 里对新列声明的 `NOT NULL` 只约束全新库，存量库该列会保持可空——这条非空约束在存量库上由应用层保证，不靠结构轨
- 写后端查询前先读本文件取权威表/列/索引，勿猜结构
- 破坏性变更（删表/删列/改名/改列类型）分两步走：先加新列并双写并发布，下个版本才从本文件移除旧列、并在 `sqlchange/` 数据轨补回填；发布前的结构 diff 会拦住未走两步的破坏性变更
- 发布成功后，平台按版本归档本文件的快照到 `releases/<版本>/schema.sql`，作为下次 diff 基线
