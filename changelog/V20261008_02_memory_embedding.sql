-- changelog/V20261008_02_memory_embedding.sql
-- 日期：2026-10-08  用途：向量召回就位（spec §5 / ticket 005）——model_config 分型 + embedding 定维 + 向量索引
-- 影响：model_config 加 kind 列并重建「默认位」唯一索引；workspace_memory.embedding 收敛为 vector(1024) 并建 HNSW 索引。
-- 幂等：ADD COLUMN / CREATE INDEX 均 IF NOT EXISTS，DROP INDEX 亦 IF EXISTS，可在存量库安全重放。
-- 例外：下面那条 ALTER COLUMN ... TYPE vector(1024) 没有守卫形式（PG 不提供 IF-TYPE），
-- 重放它 = 全表重写 + 持 ACCESS EXCLUSIVE 锁；当前该列全为 NULL 故重写无成本，
-- 但一旦有数据且维度已是 1024，重放只是一次昂贵的空转，若维度不符则会直接失败并拖挂 run_migrations。
--
-- 为什么必须重建默认位索引：V20260925_02 建的 ux_model_default_per_owner 只按 owner_user_id 约束，
-- 等于「每用户跨全表至多一个默认」。加了 kind 之后这条会直接拦死「默认 chat + 默认 embedding」这对
-- 正常配置，故先 DROP 旧索引再建 (owner_user_id, kind) 的部分唯一索引。
-- 沿用 NULLS DISTINCT（默认行为）：无主遗留行之间的默认位仍由应用层清位语句兜住，与旧索引一致。
--
-- 维度 1024 是**破坏性约束**（D-3）：换 embedding 模型若维度不同（如 1536），必须新写一份迁移
-- ALTER COLUMN ... TYPE vector(N) 并全量重算历史向量，不能指望运行期自适应。
-- 常量与 app/agent/embeddings.py::EMBEDDING_DIM 必须一致，改一处就要改另一处。
-- 无限维 vector 无法建向量索引、且不同维度混存会让 <=> 直接抛 SQL 错，故本票把列定死。
--
-- 注意：app/db.py::split_statements 会剥掉 -- 注释并按 ; 切分，故本文件禁止出现 $$ 块或含分号的字符串字面量。

-- kind 列的取值（'chat' | 'embedding'）由应用层校验：ADD CONSTRAINT 没有 IF NOT EXISTS 形态，
-- 放进本文件就会破坏幂等重放，故不加 CHECK，改在 app/api/models.py 与 model_resolver 两侧把关。
ALTER TABLE model_config ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'chat';

DROP INDEX IF EXISTS ux_model_default_per_owner;

CREATE UNIQUE INDEX IF NOT EXISTS ux_model_default_per_owner_kind
    ON model_config (owner_user_id, kind) WHERE is_default;

-- 无守卫：见文件头的重放代价说明，改维度和建索引一样要成对动 EMBEDDING_DIM。
ALTER TABLE workspace_memory ALTER COLUMN embedding TYPE vector(1024);

CREATE INDEX IF NOT EXISTS ix_ws_memory_embedding
    ON workspace_memory USING hnsw (embedding vector_cosine_ops);
