-- 日期：2026-11-10  用途：Agent 工具调用度量（每次工具调用一行，旁路写入，可随时清空）
-- 为什么带 session_id 外键并级联删除：度量是会话的附属事实，会话删了就没有回看的意义，
-- 也避免测试/清理会话后留孤儿行。查询侧按 created_at 走时间窗口，按 session → workspace 做归属过滤。
CREATE TABLE IF NOT EXISTS agent_tool_metric (
    id           BIGSERIAL PRIMARY KEY,
    session_id   BIGINT      NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    tool_name    VARCHAR(100) NOT NULL,
    ok           BOOLEAN     NOT NULL,
    denied       BOOLEAN     NOT NULL DEFAULT FALSE,
    truncated    BOOLEAN     NOT NULL DEFAULT FALSE,
    duration_ms  INT         NOT NULL,
    result_chars INT         NOT NULL,
    round_no     INT         NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agent_tool_metric_created
    ON agent_tool_metric (created_at);
CREATE INDEX IF NOT EXISTS ix_agent_tool_metric_session
    ON agent_tool_metric (session_id, created_at);
