-- 日期：2026-09-17
-- 用途：初始化 zerocode 全部表与默认配置
-- 影响：workspace, chat_session, message, file_change, app_config

CREATE TABLE IF NOT EXISTS workspace (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    root_path VARCHAR(500) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chat_session (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL DEFAULT '新会话',
    status VARCHAR(20) NOT NULL DEFAULT 'idle',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS message (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    role VARCHAR(20) NOT NULL,
    content TEXT,
    tool_call_id VARCHAR(100),
    tool_name VARCHAR(100),
    tool_args JSONB,
    tool_result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_message_session_seq ON message(session_id, seq);
CREATE TABLE IF NOT EXISTS file_change (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    message_id BIGINT REFERENCES message(id),
    path VARCHAR(500) NOT NULL,
    action VARCHAR(20) NOT NULL,
    before_content TEXT,
    after_content TEXT,
    diff TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_config (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO app_config (key, value) VALUES
    ('llm.base_url', '"https://api.deepseek.com/v1"'),
    ('llm.api_key', '""'),
    ('llm.model', '"deepseek-chat"'),
    ('agent.write_confirm', 'false'),
    ('agent.ask_timeout_seconds', '0'),
    ('agent.max_tool_rounds', '30'),
    ('agent.history_window', '40')
ON CONFLICT (key) DO NOTHING;
