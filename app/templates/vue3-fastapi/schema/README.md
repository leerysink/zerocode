# schema —— 结构真相（声明式·当前快照）

- `schema.sql` 是应用**表结构的唯一真相**：一份全量、幂等、可重放的 DDL，只描述「结构最终长什么样」，**不含任何数据**
- 改表结构 = **编辑本文件的目标态**，不再手写/回改结构增量文件；dev 预览与 prod 发布都会全量 fold 这一份，两边收敛到同一目标态 → 结构等价由构造保证
- 只允许幂等可重放语句：
  - `CREATE TABLE IF NOT EXISTS t (col type ...);` 建新表
  - `ALTER TABLE t ADD COLUMN IF NOT EXISTS col type ...;` 给已存在的表补列（**新列必须写成这种独立语句**，`CREATE TABLE IF NOT EXISTS` 不会给已存在的表加列）
  - `CREATE INDEX IF NOT EXISTS i ON t (...);` 建索引
- **禁止** `DROP` / `ALTER COLUMN TYPE`（属破坏性变更，见下）
- 写后端查询前先读本文件取权威表/列/索引，勿猜结构
- 破坏性变更（删表/删列/改名/改列类型）分两步走：先加新列并双写并发布，下个版本才从本文件移除旧列、并在 `sqlchange/` 数据轨补回填；发布前的结构 diff 会拦住未走两步的破坏性变更
- 发布成功后，平台按版本归档本文件的快照到 `releases/<版本>/schema.sql`，作为下次 diff 基线
