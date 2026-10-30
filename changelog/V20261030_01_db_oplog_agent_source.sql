-- changelog/V20261030_01_db_oplog_agent_source.sql
-- 日期：2026-10-30  用途：审计区分 Agent 数据库工具与人工操作台（规格 docs/specs/2026-10-30-agent-db-verification-tools.md US-16）  影响：db_operation_log
-- 幂等：IF NOT EXISTS 守卫，可在存量库安全重放。

ALTER TABLE db_operation_log
    ADD COLUMN IF NOT EXISTS source VARCHAR(10) NOT NULL DEFAULT 'console';
ALTER TABLE db_operation_log
    ADD COLUMN IF NOT EXISTS session_id BIGINT;
