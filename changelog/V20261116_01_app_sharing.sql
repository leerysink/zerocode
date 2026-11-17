-- 应用共享（规格 2026-11-16 §1）：共享关系 + 发布申请。
-- 两张表一次落齐：迁移是线性历史，应用过的文件不再改；发布申请虽由 Ticket 009 接线，
-- 但按钮出现前表就得在，免得 009 去改一份已应用的迁移。
CREATE TABLE IF NOT EXISTS app_share (
    id         BIGSERIAL PRIMARY KEY,
    app_id     BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL REFERENCES actor(id) ON DELETE CASCADE,
    created_by BIGINT NOT NULL REFERENCES actor(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (app_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_app_share_user ON app_share (user_id);

CREATE TABLE IF NOT EXISTS app_publish_request (
    id           BIGSERIAL PRIMARY KEY,
    app_id       BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    requester_id BIGINT NOT NULL REFERENCES actor(id) ON DELETE CASCADE,
    status       VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|withdrawn|cancelled
    reason       TEXT,
    decided_by   BIGINT REFERENCES actor(id),
    decided_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 同一应用同时只允许一条待审申请：并发两条会在 SELECT 预检里同时通过，唯一索引是最后兜底。
CREATE UNIQUE INDEX IF NOT EXISTS ux_app_publish_request_pending
    ON app_publish_request (app_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS ix_app_publish_request_app
    ON app_publish_request (app_id, created_at DESC);
