-- changelog/V20261008_01_workspace_memory.sql
-- 日期：2026-10-08  用途：工作区记忆首版——workspace_memory 表（spec §4 / ticket 002）
-- 影响：新建 workspace_memory 表 + (workspace_id, enabled) 查询索引；确保 vector 扩展存在。
-- 幂等：IF NOT EXISTS 守卫，可在存量库安全重放。
-- 隔离维度按 workspace（D-1）：记忆随工作区删除级联清理，不跨工作区召回（故事 18）。
-- embedding 列本票只建列不写值、也不建向量索引（真语义检索留待 005）；未配置/失败时恒为 NULL。
-- 注意：app/db.py::split_statements 会剥掉 -- 注释并按 ; 切分，故本文件禁止出现 $$ 块或含分号的字符串字面量。

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS workspace_memory (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT       NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    type         VARCHAR(20)  NOT NULL,       -- preference | decision | fact | pitfall
    title        VARCHAR(200),                 -- 面板/查重用的短标，可选
    content      TEXT         NOT NULL,
    embedding    vector,                       -- pgvector；本票留空
    enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ws_memory_ws_enabled
    ON workspace_memory (workspace_id, enabled);
