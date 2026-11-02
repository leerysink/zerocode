-- changelog/V20261030_01_actor_session.sql
-- 日期：2026-10-30  用途：登录凭据从「一账号一条 token，新登录轮换踢掉旧设备」升级为
--                   「一次登录一条会话，可多人同时在线」（推翻 spec 2026-09-25 §7 的「不做多设备并发会话」）
-- 影响：actor_session（新建）、actor（加 max_sessions；token 列退役）

CREATE TABLE IF NOT EXISTS actor_session (
    id           BIGSERIAL PRIMARY KEY,
    actor_id     BIGINT NOT NULL REFERENCES actor(id) ON DELETE CASCADE,
    token        VARCHAR(64) NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 上限修剪按「最久未活动」淘汰，续期写入也走这列
CREATE INDEX IF NOT EXISTS idx_actor_session_actor_last_seen
    ON actor_session (actor_id, last_seen_at);

-- 每账号并发会话上限；坐满时新登录挤掉最久未活动的那条，而不是让新访客吃闭门羹
ALTER TABLE actor ADD COLUMN IF NOT EXISTS max_sessions INTEGER NOT NULL DEFAULT 10;

-- 存量凭据原样搬进会话表：升级瞬间已登录的设备不掉线。
-- WHERE token IS NOT NULL：改造后新注册的行不再写 token，全量重放轨重跑本句时得跳过它们。
INSERT INTO actor_session (actor_id, token, created_at, last_seen_at)
    SELECT id, token, created_at, last_seen_at FROM actor WHERE token IS NOT NULL
    ON CONFLICT (token) DO NOTHING;

-- actor.token 退役：认证唯一入口改读 actor_session，列不再被任何代码读写。
-- 刻意只放开约束、不 DROP COLUMN——出包的重放安全审计对 DROP COLUMN 一律拒包（删列会洗掉
-- 服务器既有数据），要物理删列得另立改造过人工确认。留着这列的代价是一个空列，比拆闸门便宜。
ALTER TABLE actor ALTER COLUMN token DROP NOT NULL;
ALTER TABLE actor DROP CONSTRAINT IF EXISTS actor_token_key;
