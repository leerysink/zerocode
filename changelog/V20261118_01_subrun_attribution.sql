-- 子 run 归属（ADR-0052）：主 run 行两列恒 NULL；子 run 行带委派调用 id 与批次内序号。
-- 不建新表、不加索引（ux_message_session_seq 已覆盖读取形态），幂等可重放。
ALTER TABLE message ADD COLUMN IF NOT EXISTS parent_tool_call_id VARCHAR(100);
ALTER TABLE message ADD COLUMN IF NOT EXISTS subrun_index INT;
