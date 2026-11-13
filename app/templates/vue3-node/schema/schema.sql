-- schema/schema.sql —— 结构真相（声明式·当前快照·纯幂等 DDL）
-- 改表结构 = 编辑本文件的目标态；不要在此写任何数据（种子/回填走 sqlchange/D*.sql）。
-- 只允许幂等可重放语句：CREATE TABLE IF NOT EXISTS / ALTER TABLE ADD COLUMN IF NOT EXISTS /
-- CREATE INDEX IF NOT EXISTS。禁止 DROP / ALTER COLUMN TYPE（属破坏性，见两步走）。
-- 写后端查询前先读本文件取权威表/列/索引，勿猜结构。

CREATE TABLE IF NOT EXISTS demo_notes (
    id BIGSERIAL PRIMARY KEY
);
ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS title VARCHAR(200) NOT NULL;
ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
