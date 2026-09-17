-- V20260917_02_interaction.sql
-- 日期：2026-09-17
-- 用途：对话交互增强——模型库、会话级模型与任务快照、@展开旁路列与思考留痕、压缩摘要表
-- 影响范围：model_config（新）、compaction（新）、chat_session、message

CREATE TABLE model_config (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  base_url VARCHAR(500) NOT NULL,
  api_key VARCHAR(500) NOT NULL DEFAULT '',
  model VARCHAR(200) NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 同库唯一默认（部分唯一索引：仅约束 is_default=true 的行）
CREATE UNIQUE INDEX uk_model_default ON model_config (is_default) WHERE is_default;

ALTER TABLE chat_session
  ADD COLUMN model_config_id BIGINT REFERENCES model_config(id) ON DELETE SET NULL,
  ADD COLUMN todo_state JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE message
  ADD COLUMN context_content TEXT,
  ADD COLUMN reasoning TEXT;

CREATE TABLE compaction (
  id BIGSERIAL PRIMARY KEY,
  session_id BIGINT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  up_to_seq BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_compaction_session ON compaction (session_id, id DESC);

-- 回滚：
-- DROP TABLE compaction;
-- ALTER TABLE message DROP COLUMN reasoning, DROP COLUMN context_content;
-- ALTER TABLE chat_session DROP COLUMN todo_state, DROP COLUMN model_config_id;
-- DROP INDEX uk_model_default;
-- DROP TABLE model_config;
