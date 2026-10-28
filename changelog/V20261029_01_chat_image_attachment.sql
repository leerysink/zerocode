-- 1) 模型能力位：默认 false，显式 opt-in
ALTER TABLE model_config
  ADD COLUMN IF NOT EXISTS supports_vision BOOLEAN NOT NULL DEFAULT FALSE;

-- 2) 附件表：先上传（message_id NULL），发送时绑定；删会话/删消息都级联
CREATE TABLE IF NOT EXISTS message_attachment (
  id BIGSERIAL PRIMARY KEY,
  session_id BIGINT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  message_id BIGINT REFERENCES message(id) ON DELETE CASCADE,
  filename VARCHAR(255) NOT NULL,
  mime VARCHAR(100) NOT NULL,
  size_bytes INTEGER NOT NULL,
  storage_path TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_message_attachment_message
  ON message_attachment (message_id);

CREATE INDEX IF NOT EXISTS idx_message_attachment_session
  ON message_attachment (session_id, message_id);

CREATE INDEX IF NOT EXISTS idx_message_attachment_orphan
  ON message_attachment (created_at) WHERE message_id IS NULL;

-- 3) 全局模型能力默认值（owner NULL = 平台默认，账号可覆盖）
INSERT INTO app_config (key, value) VALUES ('llm.supports_vision', 'false')
ON CONFLICT DO NOTHING;
