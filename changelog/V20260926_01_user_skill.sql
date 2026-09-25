-- changelog/V20260926_01_user_skill.sql
-- 日期：2026-09-26  用途：账户级技能库首版——user_skill 表（spec §5 / ADR-0004）
-- 影响：新建 user_skill 表
-- 幂等：IF NOT EXISTS 守卫，可在存量库安全重放。
-- slug 唯一性按 (owner_user_id, slug) 而非全局：磁盘母本落在 users/<uid>/skills/<slug>/，
-- 跨用户同 slug 天然不同目录，全局唯一会误伤多账号（对齐 ux_workspace_owner_root 的同款裁定）。

CREATE TABLE IF NOT EXISTS user_skill (
    id            BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT       NOT NULL REFERENCES actor(id) ON DELETE CASCADE,
    slug          VARCHAR(200) NOT NULL,
    name          VARCHAR(200) NOT NULL,
    description   TEXT,
    source_type   VARCHAR(20)  NOT NULL,       -- 'url' | 'github'
    source_url    VARCHAR(1000) NOT NULL,
    subpath       VARCHAR(500),
    skill_dir     VARCHAR(1000) NOT NULL,      -- 母本目录绝对路径
    enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_skill_owner_slug
    ON user_skill (owner_user_id, slug);
CREATE INDEX IF NOT EXISTS ix_user_skill_owner_enabled
    ON user_skill (owner_user_id, enabled);
